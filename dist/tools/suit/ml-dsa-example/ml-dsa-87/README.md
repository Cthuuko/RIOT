# ML-DSA-87 example with Python cryptography and C wolfCrypt

This is the ML-DSA-87 (FIPS 204, NIST security category 5) variant of the
ML-DSA-65 example in the parent directory. ML-DSA-87 is the largest FIPS 204
parameter set, offering the highest security margin at the cost of the
biggest keys, signatures, and verify working memory — sizes that matter a
lot for constrained SUIT targets (see `SUIT_ML_DSA_87_INTEGRATION.md`).

`test_ml_dsa_key.py` (Python's `cryptography` library, >= 45 for ML-DSA)
generates an ML-DSA-87 keypair, signs a message, and emits `pubkey.h` /
`signature.h`. `wolfcrypt-sample.c` (C / wolfCrypt) then generates its own
keypair as a smoke test **and** verifies the Python-produced signature,
proving the two implementations interoperate.

## Set-up

Nothing extra to build: this example links against the wolfSSL checkout
already built in the parent directory (`../wolfssl`, configured with
`--enable-dilithium --enable-experimental CPPFLAGS=-DWOLFSSL_MLDSA_NO_CTX`
per the parent README). `--enable-dilithium` enables **all three** parameter
sets (44/65/87), so the same library serves this example unchanged.

If you are starting from a fresh clone with no built `../wolfssl`, follow
steps 1–2 of the parent directory's `README.md` first.

## Run the example

Generate keys, signature, and C headers with Python (must run before
compiling — the C sample `#include`s the generated `pubkey.h` and
`signature.h`):

```bash
python3 test_ml_dsa_key.py
```

Compile the C sample against the pre-built wolfSSL in `../wolfssl` and run it:

```bash
gcc -DWOLFSSL_MLDSA_NO_CTX wolfcrypt-sample.c -o mldsa87-example \
    -I../wolfssl -L../wolfssl/src/.libs \
    -Wl,-rpath,"$(realpath ../wolfssl/src/.libs)" -lwolfssl
./mldsa87-example
```

Expected output ends with both checks passing:

```
Signature verification: VALID            # wolfCrypt's own sign→verify
Imported signature verification: VALID   # Python-signed, wolfCrypt-verified
```

The `-rpath` bakes the library location into the binary so no
`LD_LIBRARY_PATH` is needed at run time; it also guarantees you exercise
this exact checkout rather than any system-installed `libwolfssl`.

## ML-DSA-87 quick reference

| Item | Value |
|---|---|
| wolfCrypt security category (`wc_MlDsaKey_SetParams`) | `5` |
| COSE algorithm ID (RFC 9964) | `-50` |
| Public key size | 2592 bytes |
| Private key size (expanded) | 4896 bytes |
| Signature size | 4627 bytes |
| Seed size (used by e.g. Python's `cryptography` lib) | 32 bytes |

## Interop gotcha (inherited from the ML-DSA-65 example)

Python's `cryptography` signs with the FIPS 204 *context-aware* API using an
empty context. On the wolfCrypt side that corresponds to
`wc_MlDsaKey_VerifyCtx(..., NULL, 0, ...)` — plain `wc_MlDsaKey_Verify()`
rejects these signatures. The same holds for on-device SUIT verification;
see `SUIT_ML_DSA_87_INTEGRATION.md`.

## SUIT integration

`SUIT_ML_DSA_87_INTEGRATION.md` in this directory describes what it takes to
use ML-DSA-87 for actual SUIT manifest signing and on-device verification,
building on the working ML-DSA-65 integration — and why ML-DSA-87 is
realistically out of reach for 32KB-RAM boards like `samr21-xpro`.
