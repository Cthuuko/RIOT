#!/usr/bin/env python3

#
# Copyright (C) 2026 Miguel Arcilla
#
# This file is subject to the terms and conditions of the GNU Lesser
# General Public License v2.1. See the file LICENSE in the top level
# directory for more details.
#

"""Emit a C header defining `suit_enc_seckey[32]` from an X25519 private key
PEM file: the device's static key for SUIT manifest decryption (COSE
ECDH-ES + HKDF-256). Raw bytes are RFC 7748 little-endian, matching the
EC25519_LITTLE_ENDIAN imports in sys/suit/encrypt/decrypt.c.

SECURITY: the private key ends up in the firmware image; prototype-grade
key storage only."""

import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def main():
    if len(sys.argv) != 2:
        print("usage: enckey_to_header.py <x25519_private_key.pem>")
        sys.exit(1)

    with open(sys.argv[1], 'rb') as f:
        seckey = load_pem_private_key(f.read(), None)

    if not isinstance(seckey, X25519PrivateKey):
        sys.exit("suit: SUIT_ENC_SEC must be an X25519 private key")

    raw = seckey.private_bytes_raw()
    print(f"const uint8_t suit_enc_seckey[{len(raw)}] = {{")
    print(", ".join(f"0x{b:02x}" for b in raw))
    print("};")


if __name__ == '__main__':
    main()
