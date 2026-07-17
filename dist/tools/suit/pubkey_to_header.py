#!/usr/bin/env python3

#
# Copyright (C) 2026 Freie Universität Berlin
#
# This file is subject to the terms and conditions of the GNU Lesser
# General Public License v2.1. See the file LICENSE in the top level
# directory for more details.
#

"""Emit a C header defining `public_key[][N]` from one or more SUIT public
key PEM files. Works with any algorithm `cryptography`'s
`public_bytes_raw()` supports (e.g. Ed25519, ML-DSA), reading the raw key
material without any algorithm-specific DER offset assumptions."""

import sys

from cryptography.hazmat.primitives.serialization import load_pem_public_key


def main():
    if len(sys.argv) < 2:
        print("usage: pubkey_to_header.py <pubkey.pem> [pubkey.pem ...]")
        sys.exit(1)

    raw_keys = []
    for path in sys.argv[1:]:
        with open(path, 'rb') as f:
            pubkey = load_pem_public_key(f.read())
        raw_keys.append(pubkey.public_bytes_raw())

    key_size = len(raw_keys[0])
    if any(len(k) != key_size for k in raw_keys):
        sys.exit("suit: all configured SUIT_KEY public keys must use the "
                  "same algorithm (mismatched key sizes)")

    print(f"const uint8_t public_key[][{key_size}] = {{")
    for raw_key in raw_keys:
        print(" {")
        print(", ".join(f"0x{b:02x}" for b in raw_key))
        print(" },")
    print("};")


if __name__ == '__main__':
    main()
