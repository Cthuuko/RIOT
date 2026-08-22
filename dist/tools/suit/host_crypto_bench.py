#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeat-measurement harness for the host side of a SUIT update.

The instrumented tools (`SUIT_HOST_PERF=1`, see hostperf.py) yield exactly one
sample per phase per publish. That is enough on the device -- a bare-metal
Cortex-M4 running one thread reproduces to 0.08 % -- but not here: a 20-thread
desktop under WSL2 has no such stability, and ML-DSA signing is a *rejection
sampling loop* whose iteration count depends on the data, so its cost is a
distribution rather than a number. Neither fact is visible in one sample.

This harness therefore re-runs each primitive N times over **fixed reference
bytes, identical across every tier**, and reports the distribution. Scope is
the five tiers of examples/advanced/suit_update/PERF_RESULTS_NRF52840.md, so
every number here pairs with an already-measured device number.

Output is `HOSTBENCH,` CSV plus a human-readable table, mirroring the device
report's dual format:

    HOSTBENCH,<tier>,<sig_algo>,<kem_algo>,<phase>,<n>,<median_us>,<min_us>,
              <p95_us>,<stdev_us>,<bytes>

Usage:

    ./host_crypto_bench.py --keydir examples/advanced/suit_update \\
        [--payload <file>] [--repeats 200] [--aead-repeats 50]
"""

import argparse
import os
import sys
import time

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric import mldsa
from cryptography.hazmat.primitives.asymmetric import mlkem
from cryptography.hazmat.primitives.asymmetric import utils as asymmetric_utils
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfstats import (            # noqa: E402  (path set above)
    bench_header, bench_line, constant, format_header, format_row, summarise,
)

BENCH_PREFIX = 'HOSTBENCH'

COSE_ALG_CHACHA20_POLY1305 = 24
COSE_ALG_ECDH_ES_HKDF_256 = -25
COSE_ALG_MLKEM768 = -70768

# The five tiers of PERF_RESULTS_NRF52840.md. Key paths are relative to
# --keydir; every one of these already exists in the repo.
TIERS = [
    ('T1', 'ed25519',   'x25519',      'ed25519-keys/ed25519.pem',
                                       'ed25519-keys/device_x25519.pem'),
    ('T2', 'ed25519',   'ml-kem-768',  'ed25519-keys/ed25519.pem',
                                       'ed25519-keys/device_mlkem768.pem'),
    ('T3', 'ml-dsa-44', 'x25519',      'mldsa44-keys/mldsa44.pem',
                                       'mldsa44-keys/device_x25519.pem'),
    ('T4', 'ml-dsa-65', 'ml-kem-768',  'mldsa-keys/mldsa65.pem',
                                       'mldsa-keys/device_mlkem768.pem'),
    ('T5', 'es256',     'x25519',      'es256-keys/es256.pem',
                                       'es256-keys/device_x25519.pem'),
]

# T1/T2/T5 all produce a 499 B manifest plaintext on the dongle
# (PERF_RESULTS_NRF52840.md section 1). Using that size for every tier makes
# the AEAD a pure control: identical input, identical expected cost.
REFERENCE_MANIFEST_LEN = 499
# T1's installed payload, so payload_aead throughput is comparable with the
# device's 543.0 kB/s figure over the same number of bytes.
REFERENCE_PAYLOAD_LEN = 109388

ES_HASHES = {256: hashes.SHA256, 384: hashes.SHA384, 521: hashes.SHA512}


def parse_arguments():
    here = os.path.dirname(os.path.abspath(__file__))
    default_keydir = os.path.normpath(os.path.join(
        here, '..', '..', '..', 'examples', 'advanced', 'suit_update'))
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__.split('\n\n')[0])
    parser.add_argument('--keydir', default=default_keydir,
                        help='directory holding the per-tier key directories')
    parser.add_argument('--payload', default=None,
                        help='real firmware image to use as the payload_aead '
                             'input; a deterministic pattern of the same '
                             'length as the device T1 run is used if omitted')
    parser.add_argument('--repeats', type=int, default=200,
                        help='iterations for the asymmetric phases')
    parser.add_argument('--aead-repeats', type=int, default=50,
                        help='iterations for the bulk symmetric phases')
    parser.add_argument('--warmup', type=int, default=10,
                        help='discarded iterations before each measurement')
    return parser.parse_args()


def measure(fn, repeats, warmup):
    """Run @p fn and return the per-iteration durations in microseconds.

    Each iteration is timed on its own rather than timing a batch: a batch
    mean would hide exactly the ML-DSA rejection-sampling spread this harness
    exists to expose.
    """
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    return samples


def kdf_context(recipient_protected):
    """COSE_KDF_Context (RFC 9053 section 5.2) -- byte-identical to the one
    the three encrypt_*.py tools and sys/suit/encrypt/decrypt.c build."""
    return cbor2.dumps([
        COSE_ALG_CHACHA20_POLY1305,
        [None, None, None],
        [None, None, None],
        [256, recipient_protected],
    ])


def derive_cek(shared_secret, recipient_protected):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=kdf_context(recipient_protected)).derive(shared_secret)


def enc_structure(body_protected):
    return cbor2.dumps(["Encrypt", body_protected, b''])


def load_key(path):
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), None)


def sign_callable(private_key, sig_structure):
    """The exact call suit_tool/sign.py makes, per key type."""
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        hash_cls = ES_HASHES[private_key.key_size]

        def _sign():
            der = private_key.sign(sig_structure, ec.ECDSA(hash_cls()))
            r, s = asymmetric_utils.decode_dss_signature(der)
            ssize = (private_key.key_size + 7) // 8
            return r.to_bytes(ssize, 'big') + s.to_bytes(ssize, 'big')
        return _sign

    if isinstance(private_key, (ed25519.Ed25519PrivateKey,
                                mldsa.MLDSA44PrivateKey,
                                mldsa.MLDSA65PrivateKey,
                                mldsa.MLDSA87PrivateKey)):
        return lambda: private_key.sign(sig_structure)

    raise SystemExit(f'unsupported signing key: {type(private_key).__name__}')


def kem_callable(device_key):
    """Producer-side key establishment, scoped exactly as the tools scope it:
    key agreement plus the HKDF that yields the CEK."""
    public_key = device_key.public_key()

    if isinstance(device_key, (mlkem.MLKEM768PrivateKey,
                               mlkem.MLKEM1024PrivateKey)):
        alg = COSE_ALG_MLKEM768 if isinstance(
            device_key, mlkem.MLKEM768PrivateKey) else -70769
        recipient_protected = cbor2.dumps({1: alg})

        def _kem():
            shared_secret, kem_ct = public_key.encapsulate()
            derive_cek(shared_secret, recipient_protected)
            return kem_ct
        return _kem

    recipient_protected = cbor2.dumps({1: COSE_ALG_ECDH_ES_HKDF_256})

    def _kem():
        ephemeral = X25519PrivateKey.generate()
        shared_secret = ephemeral.exchange(public_key)
        derive_cek(shared_secret, recipient_protected)
        return ephemeral.public_key().public_bytes_raw()
    return _kem


def reference_sig_structure():
    """The Sig_structure suit-tool signs: ["Signature1", protected, h'',
    payload], where the payload carries a SHA-256 digest. Its length is what
    every algorithm below actually signs."""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(b'reference SUIT manifest body')
    return cbor2.dumps([
        "Signature1",
        cbor2.dumps({1: -8}),          # protected: alg (EdDSA placeholder)
        b'',
        cbor2.dumps([-16, digest.finalize()]),
    ], canonical=True)


def reference_bytes(length):
    """Deterministic filler, so a re-run measures the same input."""
    block = bytes(range(256))
    return (block * (length // 256 + 1))[:length]


def emit(rows, tier, sig, kem, phase, stats, nbytes):
    rows.append((tier, sig, kem, phase, stats, nbytes))
    print(bench_line(BENCH_PREFIX, tier, sig, kem, phase, stats, nbytes))


def main(args):
    sig_structure = reference_sig_structure()
    manifest = reference_bytes(REFERENCE_MANIFEST_LEN)
    if args.payload:
        with open(args.payload, 'rb') as f:
            payload = f.read()
    else:
        payload = reference_bytes(REFERENCE_PAYLOAD_LEN)

    print('# host_crypto_bench: repeats={} aead_repeats={} warmup={}'.format(
        args.repeats, args.aead_repeats, args.warmup))
    print('# sig_structure={} B  manifest={} B  payload={} B'.format(
        len(sig_structure), len(manifest), len(payload)))
    print(bench_header(BENCH_PREFIX))

    rows = []
    for tier, sig, kem, sig_key_rel, dev_key_rel in TIERS:
        sig_key_path = os.path.join(args.keydir, sig_key_rel)
        dev_key_path = os.path.join(args.keydir, dev_key_rel)
        for path in (sig_key_path, dev_key_path):
            if not os.path.exists(path):
                raise SystemExit(f'{tier}: missing key {path}')

        # key_load is measured as its own phase because for ML-DSA it is not
        # free: the PEM holds only the FIPS 204 seed, expanded on load.
        emit(rows, tier, sig, kem, 'key_load',
             summarise(measure(lambda p=sig_key_path: load_key(p),
                               args.repeats, args.warmup)),
             os.path.getsize(sig_key_path))

        signing_key = load_key(sig_key_path)
        device_key = load_key(dev_key_path)

        sign = sign_callable(signing_key, sig_structure)
        emit(rows, tier, sig, kem, 'sig_sign',
             summarise(measure(sign, args.repeats, args.warmup)),
             len(sig_structure))
        # Signature size on the wire, for the tables that pair with the
        # device's `sig_verify` bytes column.
        emit(rows, tier, sig, kem, 'sig_bytes', *constant(len(sign())))

        kem_fn = kem_callable(device_key)
        emit(rows, tier, sig, kem, 'mfst_kem',
             summarise(measure(kem_fn, args.repeats, args.warmup)), 0)
        emit(rows, tier, sig, kem, 'kem_ct_bytes', *constant(len(kem_fn())))

        emit(rows, tier, sig, kem, 'mfst_digest',
             summarise(measure(
                 lambda: hashes.Hash(hashes.SHA256()).update(manifest),
                 args.repeats, args.warmup)), len(manifest))

        # Symmetric phases: the tier-invariant control. Same key material for
        # every tier, so any spread here is host noise, not cryptography.
        cek = os.urandom(32)
        body_protected = cbor2.dumps({1: COSE_ALG_CHACHA20_POLY1305})
        aad = enc_structure(body_protected)
        aead = ChaCha20Poly1305(cek)
        nonce = os.urandom(12)

        emit(rows, tier, sig, kem, 'mfst_aead',
             summarise(measure(lambda: aead.encrypt(nonce, manifest, aad),
                               args.repeats, args.warmup)), len(manifest))
        emit(rows, tier, sig, kem, 'payload_aead',
             summarise(measure(lambda: aead.encrypt(nonce, payload, aad),
                               args.aead_repeats, args.warmup)), len(payload))

    print()
    print(format_header())
    for tier, sig, kem, phase, stats, nbytes in rows:
        print(format_row(tier, phase, sig, kem, stats, nbytes))
    return 0


if __name__ == '__main__':
    sys.exit(main(parse_arguments()))
