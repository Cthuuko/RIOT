#!/usr/bin/env python3
"""Encrypt a (signed) SUIT manifest for a device via ML-KEM + ChaCha20-Poly1305.

Post-quantum variant of ../manifest-encryption/encrypt_manifest.py: the
X25519 ephemeral-static ECDH is replaced by an ML-KEM (FIPS 203)
encapsulation against the device's static ML-KEM key. Supports ML-KEM-768
and ML-KEM-1024 via --level (cryptography 48 offers no 512). Emits C
headers (device_seckey.h with the 64-byte d||z seed, device_pubkey.h,
encrypted.h, plaintext.h) consumed by wolfcrypt-sample.c to prove interop
with wolfCrypt's wc_MlKemKey_* API.

ML-KEM API per https://cryptography.io/en/latest/hazmat/primitives/asymmetric/mlkem/

Wire format (CBOR diagnostic notation) — same COSE_Encrypt shape as the
X25519 variant; a KEM has no ephemeral public key, so the encapsulation
ciphertext rides in the recipient's ciphertext field instead:

    96([                       # COSE_Encrypt
      << {1: 24} >>,           # protected: alg = ChaCha20/Poly1305
      {5: h'<nonce, 12B>'},    # unprotected: IV
      h'<ciphertext || tag>',  # ChaCha20-Poly1305 output (tag = last 16B)
      [[                       # recipients: exactly one
        << {1: <KEM alg> } >>, # -70768 = ML-KEM-768, -70769 = ML-KEM-1024
                               # (private-use IDs; IANA has no COSE alg
                               #  for ML-KEM yet)
        {},                    # unprotected: empty
        h'<KEM ct 1088|1568B>' # ML-KEM encapsulation ciphertext
      ]]
    ])

CEK = HKDF-SHA256(ikm=encapsulated shared secret, salt=empty,
                  info=COSE_KDF_Context, len=32)  per RFC 9053 section 5.2,
AAD = Enc_structure ["Encrypt", body_protected, h''].
Both `info` and AAD must be byte-identical on the wolfCrypt side.
"""

import argparse
import os
import sys

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import mlkem
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

COSE_ALG_CHACHA20_POLY1305 = 24
COSE_TAG_ENCRYPT = 96

# Private-use COSE algorithm IDs (no IANA registration for ML-KEM yet);
# must match wolfcrypt-sample.c and, later, sys/suit/encrypt/decrypt.c
LEVELS = {
    768:  {"key_cls": mlkem.MLKEM768PrivateKey,  "alg_id": -70768},
    1024: {"key_cls": mlkem.MLKEM1024PrivateKey, "alg_id": -70769},
}

DEFAULT_MESSAGE = b"SUIT manifest confidentiality via ML-KEM + ChaCha20-Poly1305"


def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('input', nargs='?', default=None,
                        help='File to encrypt (e.g. a signed SUIT manifest); '
                             'a built-in test message is used if omitted')
    parser.add_argument('--level', '-l', type=int, choices=LEVELS, default=768,
                        help='ML-KEM parameter set')
    parser.add_argument('--output', '-o', default="manifest.cose",
                        help='Encrypted COSE_Encrypt output file')
    parser.add_argument('--key', '-k', default=None,
                        help='Device ML-KEM private key PEM '
                             '(default: device_mlkem<level>.pem); '
                             'generated if missing')
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


def device_keypair(key_file, key_cls):
    """Load the device's static ML-KEM key, generating it on first run."""
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), None)
        print(f"Loaded device key from {key_file}")
    else:
        private_key = key_cls.generate()
        write_file(key_file, private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        print(f"Generated device key: {key_file}")

    # Always rewrite the headers - both levels share the same filenames, so
    # a stale header from another level would break the C sample's interop
    # check (learned the hard way: a 1024 pubkey against a 768 build).
    # The raw private key is the 64-byte FIPS 203 seed (d||z) - wolfCrypt
    # re-expands it deterministically via wc_MlKemKey_MakeKeyWithRandom().
    # SECURITY: embedding it in a header is prototype-only.
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


def encrypt(device_public_key, alg_id, plaintext):
    shared_secret, kem_ct = device_public_key.encapsulate()

    body_protected = cbor2.dumps({1: COSE_ALG_CHACHA20_POLY1305})
    recipient_protected = cbor2.dumps({1: alg_id})

    cek = derive_cek(shared_secret, recipient_protected)
    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(cek).encrypt(
        nonce, plaintext, enc_structure(body_protected))

    cose_encrypt = cbor2.CBORTag(COSE_TAG_ENCRYPT, [
        body_protected,
        {5: nonce},
        ciphertext,
        [[recipient_protected, {}, kem_ct]],
    ])
    return cbor2.dumps(cose_encrypt)


def decrypt(device_private_key, alg_id, cose_bytes):
    """Python-side reference decrypt, used as the self-test."""
    tagged = cbor2.loads(cose_bytes)
    assert tagged.tag == COSE_TAG_ENCRYPT, f"unexpected tag {tagged.tag}"
    body_protected, unprotected, ciphertext, recipients = tagged.value
    assert cbor2.loads(body_protected) == {1: COSE_ALG_CHACHA20_POLY1305}

    recipient_protected, _, kem_ct = recipients[0]
    assert cbor2.loads(recipient_protected) == {1: alg_id}

    shared_secret = device_private_key.decapsulate(kem_ct)
    cek = derive_cek(shared_secret, recipient_protected)
    return ChaCha20Poly1305(cek).decrypt(
        unprotected[5], ciphertext, enc_structure(body_protected))


def main(args):
    level = LEVELS[args.level]
    key_file = args.key or f"device_mlkem{args.level}.pem"
    print(f"SUIT manifest encryption (ML-KEM-{args.level} + HKDF-SHA256 + "
          "ChaCha20-Poly1305) - START")

    device_key = device_keypair(key_file, level["key_cls"])

    if args.input:
        with open(args.input, "rb") as f:
            plaintext = f.read()
        print(f"Encrypting {args.input} ({len(plaintext)} bytes)")
    else:
        plaintext = DEFAULT_MESSAGE
        print(f"Encrypting built-in test message ({len(plaintext)} bytes)")

    cose_bytes = encrypt(device_key.public_key(), level["alg_id"], plaintext)
    write_file(args.output, cose_bytes)
    print(f"Wrote {args.output}: {len(cose_bytes)} bytes "
          f"(container overhead {len(cose_bytes) - len(plaintext)} bytes)")

    # Headers for the self-contained C sample
    write_file("encrypted.h", format_byte_array("cose_encrypt", cose_bytes))
    write_file("plaintext.h", format_byte_array("expected_plaintext",
                                                plaintext))

    recovered = decrypt(device_key, level["alg_id"], cose_bytes)
    if recovered != plaintext:
        print("Self-test decrypt: MISMATCH")
        return 1
    print("Self-test decrypt: OK")

    print(f"SUIT manifest encryption (ML-KEM-{args.level}) - END")
    return 0


if __name__ == "__main__":
    sys.exit(main(parse_arguments()))
