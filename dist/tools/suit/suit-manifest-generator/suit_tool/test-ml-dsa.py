from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey
from cryptography.hazmat.backends.openssl.backend import backend

import logging
import binascii
LOG = logging.getLogger(__name__)

print("Trying out ML-DSA - START")
print(backend.openssl_version_text())

private_key = MLDSA65PrivateKey.generate()
signature = private_key.sign(b"my authenticated message")
public_key = private_key.public_key()
public_key.verify(signature, b"my authenticated message")

print("Trying out ML-DSA - END")
