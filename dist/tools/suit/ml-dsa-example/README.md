# ML-DSA example with Python cryptography and C wolfCrypt

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

## 3. Compile your program against it

```bash
python3 test_ml_dsa_key.py
gcc -DWOLFSSL_MLDSA_NO_CTX wolfcrypt-sample.c -o mldsa-example -lwolfssl
./mldsa-example
```

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
