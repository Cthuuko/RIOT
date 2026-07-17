# ML-DSA example with Python cryptography and C wolfCrypt

This example demonstrates ML-DSA (FIPS 204) post-quantum signatures as an
alternative to the Ed25519 keys RIOT's SUIT tooling uses by default.
`test_ml_dsa_key.py` (Python's `cryptography` library) generates a keypair,
signs a message, and emits `pubkey.h` / `signature.h`. `wolfcrypt-sample.c`
(C / wolfCrypt) then generates its own keypair as a smoke test **and** verifies
the Python-produced signature, proving the two implementations interoperate.

This crypto is now also wired into RIOT's actual SUIT manifest-signing
tooling: `suit-manifest-generator/bin/suit-tool keygen -t ml-dsa-65` and
`suit-tool sign` support ML-DSA the same way they support Ed25519 — see the
"ML-DSA (post-quantum) manifest signing" note in
`examples/advanced/suit_update/CLAUDE.md` for usage and its current
limitations (manifest signing only; on-device verification isn't
implemented yet since `pkg/libcose` has no PQC support).

## 1. Install build dependencies

```bash
sudo apt-get update
sudo apt-get install -y git autoconf libtool build-essential
```

## 2. Clone and build wolfSSL

wolfCrypt ships as part of the wolfSSL library — building wolfSSL gives you
wolfCrypt automatically.

```bash
git clone https://github.com/wolfssl/wolfssl.git
cd wolfssl/
./autogen.sh
```

### Basic build (hashing, symmetric ciphers, etc.)

```bash
./configure --enable-sha512
make
sudo make install
sudo ldconfig
```

### Build with ML-DSA (post-quantum signatures) support

```bash
./configure --enable-dilithium --enable-experimental \
             CPPFLAGS="-DWOLFSSL_MLDSA_NO_CTX"
make clean
make
sudo make install
sudo ldconfig
```

**Flag notes:**
- `--enable-dilithium` — turns on wolfCrypt's ML-DSA/Dilithium implementation.
- `--enable-experimental` — required by some wolfSSL versions to unlock PQC sources.
- `-DWOLFSSL_MLDSA_NO_CTX` — exposes the simpler, non-context-aware
  `wc_MlDsaKey_Sign()` / `wc_MlDsaKey_Verify()` calls. Without this define,
  recent wolfSSL versions default to a **context-aware** FIPS 204 API instead,
  and the no-context symbols won't exist in the compiled library.

This installs headers to `/usr/local/include/wolfssl/...` and the library
(`libwolfssl.so` / `libwolfssl.a`) to `/usr/local/lib`.

### Verify what got built

```bash
# Confirm Dilithium/ML-DSA was actually enabled in this build
grep -i dilithium /usr/local/include/wolfssl/options.h

# Confirm specific symbols exist in the compiled library
nm -D /usr/local/lib/libwolfssl.so | grep -i MlDsaKey
```

## 3. Run the example

First generate the keys, signature, and C headers with Python. The signature
generator must run before compiling, since the C sample `#include`s the
generated `pubkey.h` and `signature.h`:

```bash
python3 test_ml_dsa_key.py            # defaults to ML-DSA-65
# python3 test_ml_dsa_key.py -l 87    # or 44 / 87 for other parameter sets
```

Then compile and run the C sample against the installed wolfSSL:

```bash
gcc -DWOLFSSL_MLDSA_NO_CTX wolfcrypt-sample.c -o mldsa-example -lwolfssl
./mldsa-example
```

The sample verifies both its own freshly generated signature and the one
produced by Python (`Imported signature verification: VALID`). Note the C verify
path here is built only for ML-DSA-65, so keep the Python side at the default
level `65` when running the interop check.

If the linker can't find `-lwolfssl`, try an explicit path or re-run
`sudo ldconfig`:

```bash
gcc -DWOLFSSL_MLDSA_NO_CTX wolfcrypt-sample.c -o mldsa-example -I/usr/local/include -L/usr/local/lib -lwolfssl
```

## 4. ML-DSA-65 quick reference

| Item | Value |
|---|---|
| wolfCrypt security category | `3` (passed to `wc_MlDsaKey_SetParams`) |
| Public key size | 1952 bytes |
| Signature size | 3309 bytes |
| Seed size (used by e.g. Python's `cryptography` lib) | 32 bytes |


Minimal flow:

```c
#include <wolfssl/options.h>
#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/random.h>
#include <wolfssl/wolfcrypt/dilithium.h>

WC_RNG rng;
MlDsaKey key;

wc_InitRng(&rng);
wc_MlDsaKey_Init(&key, NULL, INVALID_DEVID);
wc_MlDsaKey_SetParams(&key, 3);          /* category 3 = ML-DSA-65 */
wc_MlDsaKey_MakeKey(&key, &rng);

/* sign / verify / import / export calls follow — see wolfcrypt_sample.c */
```

### Importing keys/signatures from raw bytes

```c
ret = wc_MlDsaKey_ImportPubRaw(&key, public_key, sizeof(public_key));
```
