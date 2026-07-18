#!/usr/bin/env python3
"""Generate an ML-DSA-87 keypair, sign a message, and emit C headers.

Writes the private/public keys (DER + raw) plus `pubkey.h` and `signature.h`,
which wolfcrypt-sample.c includes to verify the signature produced here. This
demonstrates ML-DSA-87 (FIPS 204, NIST security category 5) signing as the
largest ML-DSA parameter set (highest security margin) — a post-quantum alternative to the Ed25519
keys RIOT's SUIT tooling uses by default.
"""

import argparse
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA87PrivateKey

MESSAGE = b"HelloQuantumWorld"


def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--output', '-o', default="dsa87.pem",
                        help='Private key output file path (DER-encoded)')
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


def main(args):
    print("Trying out ML-DSA-87 - START")

    private_key = MLDSA87PrivateKey.generate()

    pk_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    pk_raw = private_key.private_bytes_raw()

    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    pub_raw = public_key.public_bytes_raw()

    signature = private_key.sign(MESSAGE)

    write_file(args.output, pk_der)
    write_file(args.output + ".raw", pk_raw)
    write_file(args.output + ".public", pub_pem)
    write_file(args.output + ".public.raw", pub_raw)

    print(f"Public key: {len(pub_raw)} bytes")   # expect 2592
    print(f"Signature:  {len(signature)} bytes")  # expect 4627

    # Sanity check: reload the private key from its 32-byte seed and confirm
    # the public key still verifies the signature (raises on failure).
    seed = pk_raw[:32]
    reloaded = MLDSA87PrivateKey.from_seed_bytes(seed)
    reloaded.public_key().verify(signature, MESSAGE)
    print("Round-trip verify from seed: OK")

    write_file("pubkey.h", format_byte_array("public_key", pub_raw))
    write_file("signature.h", format_byte_array("signature", signature))

    print("Trying out ML-DSA-87 - END")


if __name__ == "__main__":
    main(parse_arguments())
