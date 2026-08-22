#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------
# Copyright 2019 ARM Limited or its affiliates
#
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------
import cbor2 as cbor
import json

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric import mldsa
from cryptography.hazmat.primitives.asymmetric import utils as asymmetric_utils
from cryptography.hazmat.primitives import serialization as ks

from suit_tool.manifest import COSE_Sign1, COSEList, SUITDigest,\
                               SUITEnvelope, SUITBytes, SUITBWrapField, \
                               COSETaggedAuth
from suit_tool.hostperf import perf
import logging
import binascii
LOG = logging.getLogger(__name__)

# COSE algorithm name -> the identifier sys/suit/perf.c prints for the same
# algorithm, so a host log and a device log name the tier identically.
PERF_SIG_NAMES = {
    'ES256'     : 'es256',
    'ES384'     : 'es384',
    'ES512'     : 'es512',
    'EdDSA'     : 'ed25519',
    'ML-DSA-44' : 'ml-dsa-44',
    'ML-DSA-65' : 'ml-dsa-65',
    'ML-DSA-87' : 'ml-dsa-87',
}

# RFC 9053 pairs each ECDSA algorithm with a specific hash: ES256/SHA-256 on
# P-256, ES384/SHA-384 on P-384, ES512/SHA-512 on P-521. Keyed by key_type
# rather than by curve size so ES512 (P-521, a 521-bit curve) lands correctly.
ES_HASHES = {
    'ES256' : hashes.SHA256,
    'ES384' : hashes.SHA384,
    'ES512' : hashes.SHA512,
}

def get_cose_es_bytes(options, private_key, sig_val):
    ASN1_signature = private_key.sign(
        sig_val, ec.ECDSA(ES_HASHES[options.key_type]()))
    r,s = asymmetric_utils.decode_dss_signature(ASN1_signature)
    # COSE wants fixed-width r||s, each padded to the curve's field size in
    # whole bytes. P-521's 521 bits round *up* to 66 bytes - key_size//8 would
    # yield 65 and produce a signature the verifier rejects.
    ssize = (private_key.key_size + 7) // 8
    signature_bytes = r.to_bytes(ssize, byteorder='big') + s.to_bytes(ssize, byteorder='big')
    return signature_bytes

def get_cose_raw_sign_bytes(options, private_key, sig_val):
    return private_key.sign(sig_val)

def get_hsslms_bytes(options, private_key, sig_val):
    sig = private_key.sign(sig_val)
    key_file_name = options.private_key.name
    options.private_key.close()
    with open(key_file_name, 'wb') as fd:
        fd.write(private_key.serialize())
    return sig

def main(options):
    perf.set_tool('suit-tool-sign')

    # Read the manifest wrapper
    wrapper = cbor.loads(options.manifest.read())

    private_key = None
    digest = None
    private_key_buffer = options.private_key.read()
    try:
        # Loading is where a post-quantum private key costs something: the
        # ML-DSA PEM holds only the 32 B FIPS 204 seed, which the backend
        # expands into the full signing key here rather than at sign() time.
        with perf.phase('key_load', nbytes=len(private_key_buffer)):
            private_key = ks.load_pem_private_key(private_key_buffer, password=str.encode(options.password) if options.password else None, backend=default_backend())
        if isinstance(private_key, ec.EllipticCurvePrivateKey):
            # P-521 signs with SHA-512, so its COSE name is ES512, not the
            # 'ES521' a plain key_size format would produce (which matches no
            # entry in any table below and fails with a NoneType call).
            options.key_type = {
                256 : 'ES256',
                384 : 'ES384',
                521 : 'ES512',
            }.get(private_key.key_size)
            if options.key_type is None:
                LOG.critical('Unsupported EC curve size: {}'.format(
                    private_key.key_size))
                return 1
        elif isinstance(private_key, ed25519.Ed25519PrivateKey):
            options.key_type = 'EdDSA'
        elif isinstance(private_key, mldsa.MLDSA44PrivateKey):
            options.key_type = 'ML-DSA-44'
        elif isinstance(private_key, mldsa.MLDSA65PrivateKey):
            options.key_type = 'ML-DSA-65'
        elif isinstance(private_key, mldsa.MLDSA87PrivateKey):
            options.key_type = 'ML-DSA-87'
        else:
            LOG.critical('Unrecognized key: {}'.format(type(private_key).__name__))
            return 1
        digest = {
            'ES256' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
            'ES384' : hashes.Hash(hashes.SHA384(), backend=default_backend()),
            'ES512' : hashes.Hash(hashes.SHA512(), backend=default_backend()),
            'EdDSA' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
            'ML-DSA-44' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
            'ML-DSA-65' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
            'ML-DSA-87' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
        }.get(options.key_type)
    except:
        LOG.critical('Non-library key type not implemented')
        return 1

    perf.set_algos(sig=PERF_SIG_NAMES.get(options.key_type, options.key_type))

    # Counterpart of the device's `mfst_digest`: the SHA-256 that binds the
    # COSE payload to the manifest body. Same algorithm, same input, opposite
    # end of the pipeline.
    manifest_body = cbor.dumps(wrapper[SUITEnvelope.fields['manifest'].suit_key])
    with perf.phase('mfst_digest', nbytes=len(manifest_body)):
        digest.update(manifest_body)
        digest_bytes = digest.finalize()

    cose_signature = COSE_Sign1().from_json({
        'protected' : {
            'alg' : options.key_type
        },
        'unprotected' : {},
        'payload' : {
            'algorithm-id' : 'sha256',
            'digest-bytes' : digest_bytes
        }
    })

    Sig_structure = cbor.dumps([
        "Signature1",
        cose_signature.protected.to_suit(),
        b'',
        cose_signature.payload.to_suit(),
    ], canonical = True)
    LOG.debug('Signing: {}'.format(binascii.b2a_hex(Sig_structure).decode('utf-8')))

    # The headline measurement, and the counterpart of the device's
    # `sig_verify`. Note what COSE signs: the Sig_structure carrying the
    # SHA-256 *digest*, not the manifest -- so its input is the same ~50 B for
    # every algorithm, and the comparison across tiers is over byte-identical
    # input by construction.
    with perf.phase('sig_sign', nbytes=len(Sig_structure)):
        signature_bytes = {
            'ES256' : get_cose_es_bytes,
            'ES384' : get_cose_es_bytes,
            'ES512' : get_cose_es_bytes,
            'EdDSA' : get_cose_raw_sign_bytes,
            'HSS-LMS' : get_hsslms_bytes,
            'ML-DSA-44' : get_cose_raw_sign_bytes,
            'ML-DSA-65' : get_cose_raw_sign_bytes,
            'ML-DSA-87' : get_cose_raw_sign_bytes,
        }.get(options.key_type)(options, private_key, Sig_structure)

    cose_signature.signature = SUITBytes().from_suit(signature_bytes)

    auth = SUITBWrapField(COSEList)().from_suit(wrapper[SUITEnvelope.fields['auth'].suit_key])
    auth.v.append(auth.v.field.obj().from_json({
        'COSE_Sign1_Tagged' : cose_signature.to_json()
    }))
    wrapper[SUITEnvelope.fields['auth'].suit_key] = auth.to_suit()

    # Signed envelope as it goes on the wire, before manifest encryption: this
    # `bytes` figure is what the device later reports as `mfst_fetch` in a
    # plaintext build, and as the plaintext behind `mfst_aead` in an
    # encrypted one.
    with perf.phase('mfst_serialize'):
        signed = cbor.dumps(wrapper, canonical=True)
    perf.count('mfst_serialize', len(signed))
    perf.count('sig_bytes', len(signature_bytes))
    options.output_file.write(signed)
    return 0
