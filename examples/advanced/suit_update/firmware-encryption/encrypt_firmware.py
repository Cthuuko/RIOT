#!/usr/bin/env python3
"""Encrypt a firmware image for a device via X25519 + ChaCha20-Poly1305.

Host side of the firmware-encryption example, and the real host-side tool
for the SUIT firmware-encryption workflow (SUIT_FIRMWARE_ENCRYPT=1). Same
crypto pipeline and device key as ../manifest-encryption/encrypt_manifest.py
(ephemeral-static X25519, COSE "ECDH-ES + HKDF-256" alg -25, HKDF-SHA256,
ChaCha20-Poly1305 COSE alg 24) — only the container differs: the ciphertext
is DETACHED (RFC 9052 section 5.1), because the device decrypts the image
while it streams in and a firmware image never fits in a CBOR parse buffer.

Wire format of the output file (CBOR diagnostic notation):

    96([                       # COSE_Encrypt header, self-delimiting CBOR
      << {1: 24} >>,           # protected: alg = ChaCha20/Poly1305
      {5: h'<nonce, 12B>'},    # unprotected: IV
      null,                    # ciphertext detached -> streams after this
      [[                       # recipients: exactly one
        << {1: -25} >>,        # protected: alg = ECDH-ES + HKDF-256
        {-1: {1: 1, -1: 4,     # unprotected: ephemeral COSE_Key (OKP, X25519)
              -2: h'<ephemeral public key, 32B>'}},
        h''                    # no encrypted key: direct key agreement
      ]]
    ])
    || ciphertext (same length as the plaintext image)
    || Poly1305 tag (16 B)

CEK = HKDF-SHA256(ikm=X25519(eph_priv, device_pub), salt=empty,
                  info=COSE_KDF_Context, len=32)  per RFC 9053 section 5.2,
AAD = Enc_structure ["Encrypt", body_protected, h''].
Byte-identical `info`/AAD rules as the manifest container — the device
reuses the received protected-header bytes verbatim.
"""

import argparse
import io
import os
import sys
from contextlib import nullcontext

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
try:
    # post-quantum recipient (cryptography >= 48); the recipient scheme is
    # auto-detected from the device key's type, matching
    # ../manifest-encryption-mlkem/encrypt_manifest.py and the compile-time
    # dispatch in sys/suit/encrypt/decrypt.c
    from cryptography.hazmat.primitives.asymmetric import mlkem
except ImportError:
    mlkem = None
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Opt-in host-side perf checkpoints (SUIT_HOST_PERF=1), the producer-side
# counterpart of the device's SUIT_PERF=1 -- see PERFORMANCE.md section 8.
# Imported by path so this example still runs standalone outside RIOT, where
# it degrades to a silent no-op.
sys.path.append(os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "dist", "tools", "suit")))
try:
    from hostperf import perf
except ImportError:
    class _NoPerf:
        def phase(self, *args, **kwargs):
            return nullcontext()

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    perf = _NoPerf()

COSE_ALG_CHACHA20_POLY1305 = 24
COSE_ALG_ECDH_ES_HKDF_256 = -25
COSE_ALG_MLKEM768 = -70768      # private-use IDs, no IANA COSE alg yet
COSE_ALG_MLKEM1024 = -70769
COSE_TAG_ENCRYPT = 96

DEVICE_KEY_FILE = "device_x25519.pem"


def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('input', nargs='?', default=None,
                        help='Firmware image to encrypt (e.g. payload.bin or '
                             'a slot binary); a built-in 13KB patterned test '
                             'payload is used if omitted')
    parser.add_argument('--output', '-o', default="firmware.enc",
                        help='Encrypted output file (COSE header || '
                             'ciphertext || tag)')
    parser.add_argument('--key', '-k', default=DEVICE_KEY_FILE,
                        help='Device X25519 private key PEM (e.g. the '
                             'SUIT_ENC_SEC key the firmware was built with, '
                             'default: ~/.local/share/RIOT/keys/'
                             'device_x25519.pem); generated if missing')
    parser.add_argument('--no-headers', action='store_true',
                        help='Skip writing the standalone-example C headers '
                             '(encrypted.h/plaintext.h/device_*.h) — use '
                             'this when encrypting real payloads for the '
                             'SUIT workflow')
    return parser.parse_args()


def write_file(path, data):
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600),
              mode) as f:
        f.write(data)


def format_byte_array(name, data):
    lines = [f"const byte {name}[{len(data)}] = {{"]
    for i in range(0, len(data), 12):
        chunk = data[i:i + 12]
        lines.append("    " + ", ".join(f"0x{b:02x}" for b in chunk) + ",")
    lines.append("};\n")
    return "\n".join(lines)


def device_keypair(key_file, write_headers):
    """Load the device's static X25519 key, generating it on first run.

    The C-sample headers are (re)written on every run when present-key
    material is used, so a stale header can never poison the C build
    (lesson from the ML-KEM standalone example).
    """
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), None)
        print(f"Loaded device key from {key_file}")
    else:
        private_key = X25519PrivateKey.generate()
        write_file(key_file, private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        print(f"Generated device key: {key_file}")

    if write_headers:
        # SECURITY: embedding the private key in a header is prototype-only.
        write_file("device_seckey.h", format_byte_array(
            "device_seckey", private_key.private_bytes_raw()))
        write_file("device_pubkey.h", format_byte_array(
            "device_pubkey", private_key.public_key().public_bytes_raw()))
    return private_key


def kdf_context(recipient_protected):
    """COSE_KDF_Context (RFC 9053 section 5.2) used as the HKDF info."""
    return cbor2.dumps([
        COSE_ALG_CHACHA20_POLY1305,        # AlgorithmID (of the content key)
        [None, None, None],                # PartyUInfo
        [None, None, None],                # PartyVInfo
        [256, recipient_protected],        # SuppPubInfo: keyDataLength (bits),
    ])                                     #   recipient protected header


def enc_structure(body_protected):
    """Enc_structure AAD (RFC 9052 section 5.3)."""
    return cbor2.dumps(["Encrypt", body_protected, b''])


def derive_cek(shared_secret, recipient_protected):
    return HKDF(algorithm=SHA256(), length=32, salt=None,
                info=kdf_context(recipient_protected)).derive(shared_secret)


def make_recipient(device_public_key):
    """Recipient structure + shared secret, by device-key type.

    X25519: ephemeral-static ECDH, ephemeral key in the recipient's
    unprotected header. ML-KEM: encapsulation, KEM ciphertext in the
    recipient's ciphertext field (see manifest-encryption-mlkem/README.md).
    """
    if isinstance(device_public_key, X25519PublicKey):
        perf.set_algos(kem='x25519')
        ephemeral = X25519PrivateKey.generate()
        shared_secret = ephemeral.exchange(device_public_key)
        recipient_protected = cbor2.dumps({1: COSE_ALG_ECDH_ES_HKDF_256})
        ephemeral_key = {1: 1, -1: 4,      # kty: OKP, crv: X25519
                         -2: ephemeral.public_key().public_bytes_raw()}
        return shared_secret, \
            [recipient_protected, {-1: ephemeral_key}, b'']

    if mlkem is not None and isinstance(device_public_key,
                                        mlkem.MLKEM768PublicKey):
        alg_id = COSE_ALG_MLKEM768
        perf.set_algos(kem='ml-kem-768')
    elif mlkem is not None and isinstance(device_public_key,
                                          mlkem.MLKEM1024PublicKey):
        alg_id = COSE_ALG_MLKEM1024
        perf.set_algos(kem='ml-kem-1024')
    else:
        raise SystemExit(f"unsupported device key type: "
                         f"{type(device_public_key).__name__}")
    recipient_protected = cbor2.dumps({1: alg_id})
    shared_secret, kem_ct = device_public_key.encapsulate()
    return shared_secret, [recipient_protected, {}, kem_ct]


def encrypt(device_public_key, plaintext):
    """Return header || ciphertext || tag (detached-ciphertext container)."""
    body_protected = cbor2.dumps({1: COSE_ALG_CHACHA20_POLY1305})

    # Producer half of the device's `payload_kem`, scoped identically to
    # `mfst_kem` in the manifest tools: key agreement plus the HKDF that
    # turns the shared secret into the CEK.
    with perf.phase('payload_kem'):
        shared_secret, recipient = make_recipient(device_public_key)
        recipient_protected = recipient[0]
        cek = derive_cek(shared_secret, recipient_protected)

    nonce = os.urandom(12)
    # cryptography returns ciphertext||tag — exactly the detached stream.
    # One shot here against 3,400+ streamed chunks on the device: the same
    # ChaCha20-Poly1305, in the two shapes PERFORMANCE.md section 2C names.
    with perf.phase('payload_aead', nbytes=len(plaintext)):
        ct_and_tag = ChaCha20Poly1305(cek).encrypt(
            nonce, plaintext, enc_structure(body_protected))

    header = cbor2.dumps(cbor2.CBORTag(COSE_TAG_ENCRYPT, [
        body_protected,
        {5: nonce},
        None,                              # detached ciphertext
        [recipient],
    ]))
    return header, ct_and_tag


def decrypt(device_private_key, blob):
    """Python-side reference decrypt, used as the self-test."""
    fp = io.BytesIO(blob)
    tagged = cbor2.CBORDecoder(fp).decode()
    header_len = fp.tell()
    assert tagged.tag == COSE_TAG_ENCRYPT, f"unexpected tag {tagged.tag}"
    body_protected, unprotected, detached, recipients = tagged.value
    assert detached is None, "ciphertext must be detached (null)"
    assert cbor2.loads(body_protected) == {1: COSE_ALG_CHACHA20_POLY1305}

    recipient_protected, recipient_unprotected, recipient_ct = recipients[0]
    recipient_alg = cbor2.loads(recipient_protected)[1]
    if recipient_alg == COSE_ALG_ECDH_ES_HKDF_256:
        ephemeral_pub = recipient_unprotected[-1][-2]
        shared_secret = device_private_key.exchange(
            X25519PublicKey.from_public_bytes(ephemeral_pub))
    elif recipient_alg in (COSE_ALG_MLKEM768, COSE_ALG_MLKEM1024):
        shared_secret = device_private_key.decapsulate(recipient_ct)
    else:
        raise SystemExit(f"unknown recipient alg {recipient_alg}")
    cek = derive_cek(shared_secret, recipient_protected)
    ct_and_tag = blob[header_len:]
    return ChaCha20Poly1305(cek).decrypt(
        unprotected[5], ct_and_tag, enc_structure(body_protected))


def test_payload():
    """~13KB patterned pseudo-firmware: forces the streaming C sample
    through many 64-byte chunks and a header/ciphertext chunk straddle."""
    block = bytes(range(256))
    return b"RIOT-FW-ENC-TEST" + block * 50 + b"END-OF-IMAGE"


def main(args):
    print("SUIT firmware encryption (X25519 + HKDF-SHA256 + "
          "ChaCha20-Poly1305, detached ciphertext) - START")
    perf.set_tool('encrypt-firmware')

    write_headers = not args.no_headers
    device_key = device_keypair(args.key, write_headers)

    if args.input:
        with open(args.input, "rb") as f:
            plaintext = f.read()
        print(f"Encrypting {args.input} ({len(plaintext)} bytes)")
    else:
        plaintext = test_payload()
        print(f"Encrypting built-in test payload ({len(plaintext)} bytes)")

    header, ct_and_tag = encrypt(device_key.public_key(), plaintext)
    blob = header + ct_and_tag
    write_file(args.output, blob)
    print(f"Wrote {args.output}: {len(blob)} bytes "
          f"(header {len(header)} + ciphertext {len(plaintext)} + tag 16)")
    # The device prints this as `suit: decrypting payload (header N bytes)`:
    # 74 B for X25519, 1,126 B for ML-KEM-768. Recorded here so the KEM's
    # second wire cost per update is in the host CSV too.
    perf.count('payload_hdr', len(header))

    if write_headers:
        # Headers for the self-contained C sample
        write_file("encrypted.h", format_byte_array("enc_stream", blob))
        write_file("plaintext.h", format_byte_array("expected_plaintext",
                                                    plaintext))

    recovered = decrypt(device_key, blob)
    if recovered != plaintext:
        print("Self-test decrypt: MISMATCH")
        return 1
    print("Self-test decrypt: OK")

    print("SUIT firmware encryption - END")
    return 0


if __name__ == "__main__":
    sys.exit(main(parse_arguments()))
