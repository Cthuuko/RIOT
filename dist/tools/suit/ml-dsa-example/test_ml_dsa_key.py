#!/usr/bin/env python3

from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey
from cryptography.hazmat.primitives import serialization
import logging
import binascii
import argparse
import json
import os
import uuid
import base64

def parse_arguments():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--level', '-l', help='Security Level of ML_DSA. Valid Values: [0, 1, 2]',
                        default="0")
    parser.add_argument('--output', '-o', default="dsa.pem",
                        help='Manifest output binary file path')
    return parser.parse_args()


def main(args):
    LOG = logging.getLogger(__name__)

    print("Trying out ML-DSA - START")

    output = args.output
    output_raw = output + ".raw"
    output_public = output + ".public"
    output_public_raw = output + ".public.raw"
    private_key = MLDSA65PrivateKey.generate()
    pk_bytes = private_key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pk_bytes_raw = private_key.private_bytes_raw()


    context = b"SUIT-Update"
    message_to_verify = b"HelloQuantumWorld"

    signature = private_key.sign(message_to_verify)

    pem = """MDQCAQAwCwYJYIZIAWUDBAMSBCKAIK3vddkpQYT5z6bvP6j6Trdd0KzBamDzQUpR"""

    raw = base64.b64decode(pem)

    #print("Private Key Bytes RAW: ", pk_bytes_raw)
    #print(len(pk_bytes_raw))
    #print("PEM: ", pk_bytes)
    #print(len(pk_bytes))
    #print("PEMtoRAW: ", raw)
    #print(len(raw))

    #print(private_key)



    with open(os.open(output, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
        f.write(pk_bytes)
    
    with open(os.open(output_raw, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
        f.write(pk_bytes_raw)

    with open(os.open(output_raw, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
        f.write(pk_bytes_raw)

    public_key = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    pub_bytes_raw = private_key.public_key().public_bytes_raw()

    with open(os.open(output_public, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
        f.write(public_key)

    with open(os.open(output_public_raw, os.O_CREAT | os.O_WRONLY, 0o600), "wb") as f:
        f.write(pub_bytes_raw)

    print("Public Key Bytes RAW First 32 bytes: ", pub_bytes_raw[:32])
    print(len(pub_bytes_raw))
    print("Signature Bytes RAW First 32 bytes: ", signature[:32])
    print(len(signature))

    with open(output_raw, "rb") as f:
        der_bytes = f.read()
        #print(der_bytes[:32])
        #print(len(der_bytes[:32]))
        seed_bytes = der_bytes[:32]

    private_key_loaded = MLDSA65PrivateKey.from_seed_bytes(seed_bytes)
    public_key_loaded = private_key_loaded.public_key()

    public_key_loaded.verify(signature, message_to_verify)

    print("Trying out ML-DSA - END")
    

    lines = []
    lines.append(f"const byte public_key[{len(pub_bytes_raw)}] = {{")
 
    for i in range(0, len(pub_bytes_raw), 4):
        chunk = pub_bytes_raw[i:i + 4]
        hex_values = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_values},")
 
    lines.append("};")
    pub_key_arr = "\n".join(lines)

    with open(os.open("pubkey.h", os.O_CREAT | os.O_WRONLY, 0o600), "w") as f:
        f.write(pub_key_arr)

    lines = []
    lines.append(f"const byte signature[{len(signature)}] = {{")
 
    for i in range(0, len(signature), 4):
        chunk = signature[i:i + 4]
        hex_values = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_values},")
 
    lines.append("};")
    sign_arr = "\n".join(lines)

    with open(os.open("signature.h", os.O_CREAT | os.O_WRONLY, 0o600), "w") as f:
        f.write(sign_arr)


if __name__ == "__main__":
    _args = parse_arguments()
    main(_args)
