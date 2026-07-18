#!/usr/bin/env python3
"""Encrypt a (signed) SUIT manifest for a device via X25519 + ChaCha20-Poly1305.

Host side of the manifest-encryption example. Generates the device's static
X25519 keypair (once), then encrypts an input file into an RFC 9770-style
COSE_Encrypt container using ephemeral-static ECDH (COSE "ECDH-ES +
HKDF-256", alg -25) with ChaCha20-Poly1305 (COSE alg 24) as the content
cipher. Emits C headers (device_seckey.h, device_pubkey.h, encrypted.h,
plaintext.h) that wolfcrypt-sample.c includes to prove the device side
(wolfCrypt) can decrypt what Python's `cryptography` library encrypted.

X25519 API per https://cryptography.io/en/48.0.0/hazmat/primitives/asymmetric/x25519/

Wire format (CBOR diagnostic notation):

    96([                       # COSE_Encrypt
      << {1: 24} >>,           # protected: alg = ChaCha20/Poly1305
      {5: h'<nonce, 12B>'},    # unprotected: IV
      h'<ciphertext || tag>',  # ChaCha20-Poly1305 output (tag = last 16B)
      [[                       # recipients: exactly one
        << {1: -25} >>,        # protected: alg = ECDH-ES + HKDF-256
        {-1: {1: 1, -1: 4,     # unprotected: ephemeral COSE_Key (OKP, X25519)
              -2: h'<ephemeral public key, 32B>'}},
        h''                    # no encrypted key: direct key agreement
      ]]
    ])

CEK = HKDF-SHA256(ikm=X25519(eph_priv, device_pub), salt=empty,
                  info=COSE_KDF_Context, len=32)  per RFC 9053 section 5.2,
AAD = Enc_structure ["Encrypt", body_protected, h''].
Both `info` and AAD must be byte-identical on the wolfCrypt side.
"""

import argparse
import os
import sys

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

COSE_ALG_CHACHA20_POLY1305 = 24
COSE_ALG_ECDH_ES_HKDF_256 = -25
COSE_TAG_ENCRYPT = 96

DEVICE_KEY_FILE = "device_x25519.pem"
DEFAULT_MESSAGE = b"SUIT manifest confidentiality via X25519 + ChaCha20-Poly1305"


def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('input', nargs='?', default=None,
                        help='File to encrypt (e.g. a signed SUIT manifest); '
                             'a built-in test message is used if omitted')
    parser.add_argument('--output', '-o', default="manifest.cose",
                        help='Encrypted COSE_Encrypt output file')
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


def device_keypair():
    """Load the device's static X25519 key, generating it on first run."""
    if os.path.exists(DEVICE_KEY_FILE):
        with open(DEVICE_KEY_FILE, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), None)
        print(f"Loaded device key from {DEVICE_KEY_FILE}")
        return private_key

    private_key = X25519PrivateKey.generate()
    write_file(DEVICE_KEY_FILE, private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    # Raw 32-byte scalars for the C sample / later firmware embedding.
    # SECURITY: embedding the private key in a header is prototype-only.
    write_file("device_seckey.h", format_byte_array(
        "device_seckey", private_key.private_bytes_raw()))
    write_file("device_pubkey.h", format_byte_array(
        "device_pubkey", private_key.public_key().public_bytes_raw()))
    print(f"Generated device key: {DEVICE_KEY_FILE}, "
          "device_seckey.h, device_pubkey.h")
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


def encrypt(device_public_key, plaintext):
    ephemeral = X25519PrivateKey.generate()
    shared_secret = ephemeral.exchange(device_public_key)

    body_protected = cbor2.dumps({1: COSE_ALG_CHACHA20_POLY1305})
    recipient_protected = cbor2.dumps({1: COSE_ALG_ECDH_ES_HKDF_256})

    cek = derive_cek(shared_secret, recipient_protected)
    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(cek).encrypt(
        nonce, plaintext, enc_structure(body_protected))

    ephemeral_key = {1: 1, -1: 4,          # kty: OKP, crv: X25519
                     -2: ephemeral.public_key().public_bytes_raw()}
    cose_encrypt = cbor2.CBORTag(COSE_TAG_ENCRYPT, [
        body_protected,
        {5: nonce},
        ciphertext,
        [[recipient_protected, {-1: ephemeral_key}, b'']],
    ])
    return cbor2.dumps(cose_encrypt)


def decrypt(device_private_key, cose_bytes):
    """Python-side reference decrypt, used as the self-test."""
    tagged = cbor2.loads(cose_bytes)
    assert tagged.tag == COSE_TAG_ENCRYPT, f"unexpected tag {tagged.tag}"
    body_protected, unprotected, ciphertext, recipients = tagged.value
    assert cbor2.loads(body_protected) == {1: COSE_ALG_CHACHA20_POLY1305}

    recipient_protected, recipient_unprotected, _ = recipients[0]
    assert cbor2.loads(recipient_protected) == {1: COSE_ALG_ECDH_ES_HKDF_256}
    ephemeral_pub = recipient_unprotected[-1][-2]

    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PublicKey,
    )
    shared_secret = device_private_key.exchange(
        X25519PublicKey.from_public_bytes(ephemeral_pub))
    cek = derive_cek(shared_secret, recipient_protected)
    return ChaCha20Poly1305(cek).decrypt(
        unprotected[5], ciphertext, enc_structure(body_protected))


def main(args):
    print("SUIT manifest encryption (X25519 + HKDF-SHA256 + "
          "ChaCha20-Poly1305) - START")

    device_key = device_keypair()

    if args.input:
        with open(args.input, "rb") as f:
            plaintext = f.read()
        print(f"Encrypting {args.input} ({len(plaintext)} bytes)")
    else:
        plaintext = DEFAULT_MESSAGE
        print(f"Encrypting built-in test message ({len(plaintext)} bytes)")

    cose_bytes = encrypt(device_key.public_key(), plaintext)
    write_file(args.output, cose_bytes)
    print(f"Wrote {args.output}: {len(cose_bytes)} bytes "
          f"(container overhead {len(cose_bytes) - len(plaintext)} bytes)")

    # Headers for the self-contained C sample
    write_file("encrypted.h", format_byte_array("cose_encrypt", cose_bytes))
    write_file("plaintext.h", format_byte_array("expected_plaintext",
                                                plaintext))

    recovered = decrypt(device_key, cose_bytes)
    if recovered != plaintext:
        print("Self-test decrypt: MISMATCH")
        return 1
    print("Self-test decrypt: OK")

    print("SUIT manifest encryption - END")
    return 0


if __name__ == "__main__":
    sys.exit(main(parse_arguments()))
