# SUIT manifest encryption example — X25519 + HKDF-SHA256 + ChaCha20-Poly1305

Standalone host-only proof of the crypto pipeline that will later encrypt
SUIT manifests end-to-end: the host encrypts for a specific device using
ephemeral-static X25519 ECDH, and the device decrypts with wolfCrypt before
handing the plaintext to the SUIT parser. This example mirrors the structure
of `dist/tools/suit/ml-dsa-example/ml-dsa-44/` (Python `cryptography` on one
side, wolfCrypt on the other, interop proven by headers passed between them).

`encrypt_manifest.py` generates the device's static X25519 keypair (using
the [cryptography.io X25519 API](https://cryptography.io/en/48.0.0/hazmat/primitives/asymmetric/x25519/)),
encrypts a payload (a built-in test message, or any file — e.g. a signed
SUIT manifest — passed as an argument) into a COSE_Encrypt container, and
self-tests by decrypting it in Python. `wolfcrypt-sample.c` then parses the
container with nanocbor and decrypts it with wolfCrypt, proving the two
implementations interoperate. It also verifies that a tampered ciphertext is
rejected by the Poly1305 tag.

## Wire format

RFC 9770-style COSE_Encrypt (CBOR tag 96). COSE_Encrypt0 cannot carry the
sender's ephemeral public key, so the single-recipient COSE_Encrypt form is
used (CBOR diagnostic notation):

```
96([
  << {1: 24} >>,           / protected: alg = ChaCha20/Poly1305        /
  {5: h'...'},             / unprotected: 12-byte nonce (IV)           /
  h'...',                  / ciphertext || 16-byte Poly1305 tag        /
  [[                       / one recipient                             /
    << {1: -25} >>,        / protected: alg = ECDH-ES + HKDF-256       /
    {-1: {1: 1, -1: 4,     / unprotected: ephemeral COSE_Key           /
          -2: h'...'}},    /   (kty OKP, crv X25519, x = 32-byte pub)  /
    h''                    / no encrypted key: direct key agreement    /
  ]]
])
```

Key derivation (RFC 9053 §5.2) and AAD (RFC 9052 §5.3):

```
shared = X25519(ephemeral_priv, device_static_pub)     # host
       = X25519(device_static_priv, ephemeral_pub)     # device
CEK    = HKDF-SHA256(ikm=shared, salt=empty, len=32,
                     info=[24, [null,null,null], [null,null,null],
                           [256, << {1: -25} >>]])     # COSE_KDF_Context
AAD    = ["Encrypt", << {1: 24} >>, h'']               # Enc_structure
```

The `info` and AAD CBOR must be **byte-identical** on both sides. The C side
re-encodes them with nanocbor, reusing the *received* protected-header bytes
verbatim rather than re-serializing its own — the same class of interop
lesson as the ML-DSA `VerifyCtx` gotcha.

## Set-up

The example links against the wolfSSL checkout of the ML-DSA example,
which must be (re)configured with Curve25519 support on top of its existing
ML-DSA flags:

```bash
cd ../../../../dist/tools/suit/ml-dsa-example/wolfssl
./configure --enable-dilithium --enable-experimental --enable-curve25519 \
    CPPFLAGS=-DWOLFSSL_MLDSA_NO_CTX
make -j4
```

Python needs `cryptography` and `cbor2` (the standard SUIT-tooling
prerequisites; note the `suit_update` example venv may lack `cbor2` — the
system `python3` from the SUIT setup has both).

## Run the example

Generate the device key, encrypt, and self-test (must run before compiling —
the C sample `#include`s the generated `encrypted.h`, `device_seckey.h` and
`plaintext.h`):

```bash
python3 encrypt_manifest.py                # built-in test message
python3 encrypt_manifest.py some_file.bin  # or any file, e.g. a signed manifest
```

Compile the C sample against the pre-built wolfSSL and RIOT's nanocbor
checkout, and run it:

```bash
gcc wolfcrypt-sample.c \
    ../../../../build/pkg/nanocbor/src/decoder.c \
    ../../../../build/pkg/nanocbor/src/encoder.c \
    -o manifest-encryption-example \
    -I../../../../build/pkg/nanocbor/include \
    -I../../../../dist/tools/suit/ml-dsa-example/wolfssl \
    -L../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs \
    -Wl,-rpath,"$(realpath ../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs)" \
    -lwolfssl
./manifest-encryption-example
```

Expected output ends with both checks passing:

```
Python-encrypted, wolfCrypt-decrypted: MATCH
Tampered ciphertext rejected: YES (ret -213)
```

(`build/pkg/nanocbor` exists after any RIOT build that uses nanocbor, e.g.
the suit_update example itself; `-213` is wolfCrypt's AEAD authentication
failure.)

## Interop gotchas (the hard-won details)

- **X25519 endianness**: wolfCrypt's default import/export byte order is
  big-endian; cryptography.io uses RFC 7748 little-endian raw bytes. All
  wolfCrypt calls must use the `_ex` variants with `EC25519_LITTLE_ENDIAN`.
- **Curve25519 blinding needs an RNG**: this wolfSSL build defaults to
  `WOLFSSL_CURVE25519_BLINDING` (side-channel hardening of the C-only
  implementation). Without `wc_curve25519_set_rng()` on the private key,
  `wc_curve25519_shared_secret_ex()` fails with `BAD_FUNC_ARG` (-173).
- **AAD/KDF-context byte-exactness**: any serialization difference (map
  ordering, non-minimal lengths) between the two sides silently derives a
  different CEK or fails the tag. Reuse received header bytes verbatim.

## Security caveats (prototype!)

- `device_seckey.h` embeds the device's **private** key in a C header; on a
  real device it belongs in protected storage. Do not commit generated
  `device_x25519.pem` / `device_seckey.h` for anything but throwaway keys.
- Encryption provides confidentiality only; authenticity still comes from
  the SUIT COSE_Sign1 signature (sign-then-encrypt on the host,
  decrypt-then-verify on the device). ECDH-ES gives no sender
  authentication by itself.

## Container overhead

For SUIT integration budgeting: the COSE_Encrypt wrapper adds ~91 bytes for
a minimal message (32 B ephemeral key + 12 B nonce + 16 B tag + ~31 B CBOR
framing; a few more once byte-string length fields grow with a KB-sized
manifest) — the basis for the `SUIT_MANIFEST_BUFSIZE` bump planned in
`../MANIFEST_ENCRYPTION_PLAN.md`.

## SUIT integration

`../MANIFEST_ENCRYPTION_PLAN.md` tracks the full plan: device-side decrypt
hook in `sys/suit/transport/worker.c` (in-place decrypt of `_manifest_buf`
before `suit_parse()`), the `SUIT_MANIFEST_ENCRYPT` opt-out flag, host
tooling, and samr21-xpro RAM/flash/stack feasibility.
