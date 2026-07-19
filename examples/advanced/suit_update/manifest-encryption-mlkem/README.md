# SUIT manifest encryption example — ML-KEM + HKDF-SHA256 + ChaCha20-Poly1305

Post-quantum variant of [`../manifest-encryption/`](../manifest-encryption/README.md):
the X25519 ephemeral-static ECDH is replaced by an **ML-KEM** (FIPS 203)
encapsulation against the device's static ML-KEM key. Everything else —
COSE_Encrypt container, HKDF-SHA256 key derivation, ChaCha20-Poly1305
AEAD, byte-exactness rules — is identical, so the two variants can later
coexist on-device behind a single algorithm dispatch
(`MLKEM_ENCRYPTION_PLAN.md`).

`encrypt_manifest.py` (Python `cryptography` 48,
[ML-KEM API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/mlkem/))
generates the device keypair, encapsulates, encrypts, and self-tests;
`wolfcrypt-sample.c` (wolfCrypt `wc_MlKemKey_*`) re-expands the device key
from its 64-byte seed, **proves the expansion matches Python's** (public-key
comparison), decapsulates, and decrypts. Both **ML-KEM-768 and ML-KEM-1024**
are supported (`--level` / `-DMLKEM_LEVEL`; `cryptography` offers no 512).

## Wire format

Same COSE_Encrypt (tag 96) as the X25519 variant; a KEM has no ephemeral
public key, so the encapsulation ciphertext rides in the recipient's
ciphertext field (empty in the X25519 variant):

```
96([
  << {1: 24} >>,           / protected: alg = ChaCha20/Poly1305        /
  {5: h'...'},             / unprotected: 12-byte nonce (IV)           /
  h'...',                  / ciphertext || 16-byte Poly1305 tag        /
  [[                       / one recipient                             /
    << {1: -70768} >>,     / ML-KEM-768 (-70769 = ML-KEM-1024) —       /
                           / private-use IDs, IANA has no COSE alg     /
                           / for ML-KEM yet                            /
    {},                    / unprotected: empty                        /
    h'<KEM ct>'            / encapsulation ciphertext (1088 | 1568 B)  /
  ]]
])
```

```
ss  = ML-KEM.Encaps(device_ek)          # host  (ct -> container)
    = ML-KEM.Decaps(device_dk, ct)      # device
CEK = HKDF-SHA256(ikm=ss, salt=empty, len=32,
                  info=[24, [null,null,null], [null,null,null],
                        [256, << {1: <KEM alg>} >>]])
AAD = ["Encrypt", << {1: 24} >>, h'']
```

The `info`/AAD CBOR must be byte-identical on both sides (same rule and
same code as the X25519 example).

## Quick reference

| Item | ML-KEM-768 | ML-KEM-1024 |
|---|---|---|
| NIST security category | 3 | 5 |
| COSE alg ID (private use, this project) | -70768 | -70769 |
| Encapsulation key (public) | 1184 B | 1568 B |
| KEM ciphertext | 1088 B | 1568 B |
| Seed (= embedded device key) | 64 B | 64 B |
| Shared secret | 32 B | 32 B |
| Container overhead vs. plaintext | ~1144 B | ~1624 B |
| wolfCrypt type (`wc_MlKemKey_Init`) | `WC_ML_KEM_768` | `WC_ML_KEM_1024` |

## Set-up

Nothing to rebuild: the wolfSSL checkout at
`../../../../dist/tools/suit/ml-dsa-example/wolfssl` already has ML-KEM
compiled in (`WOLFSSL_HAVE_MLKEM` in its `options.h` — present since the
checkout's base version, no extra configure flag). Python needs
`cryptography` >= 48 and `cbor2`.

## Run the example

Generate the key, encrypt, and self-test (must run before compiling — the
C sample `#include`s the generated headers, and **the headers are shared
between levels**, so always rerun the Python step when switching level):

```bash
python3 encrypt_manifest.py --level 768     # or --level 1024
python3 encrypt_manifest.py --level 768 some_file.bin   # e.g. a signed manifest
```

Compile against the pre-built wolfSSL and RIOT's nanocbor checkout, and run:

```bash
W=../../../../dist/tools/suit/ml-dsa-example/wolfssl
gcc wolfcrypt-sample.c \
    ../../../../build/pkg/nanocbor/src/decoder.c \
    ../../../../build/pkg/nanocbor/src/encoder.c \
    -DMLKEM_LEVEL=768 -o mlkem768-example \
    -I../../../../build/pkg/nanocbor/include \
    -I$W -L$W/src/.libs -Wl,-rpath,"$(realpath $W/src/.libs)" -lwolfssl
./mlkem768-example
```

(Repeat with `--level 1024` / `-DMLKEM_LEVEL=1024 -o mlkem1024-example`.)

Expected output ends with all three checks passing:

```
Seed-derived public key matches Python's: OK
Python-encrypted, wolfCrypt-decrypted: MATCH
Tampered AEAD ciphertext rejected: YES (ret -213)
Tampered KEM ciphertext rejected: YES (implicit rejection -> tag error -213)
```

## Interop notes (the details that matter)

- **Seed-based key interop**: `cryptography`'s `private_bytes_raw()` is the
  64-byte FIPS 203 seed (d‖z); wolfCrypt's
  `wc_MlKemKey_MakeKeyWithRandom(key, seed, 64)` re-expands it
  deterministically (Alg 13, `(rho,sigma) = G(d‖k)` — final ML-KEM, not
  draft Kyber). The sample's public-key comparison proves the two
  expansions agree, and keeps the embedded key at 64 B instead of the
  2.4/3.2 KB expanded decapsulation key.
- **Implicit rejection**: FIPS 203 decapsulation never errors on a
  tampered KEM ciphertext — it silently returns a *different* shared
  secret, so the failure surfaces as a Poly1305 tag error after HKDF.
  Don't expect (or test for) a decapsulation error code.
- **Stale headers**: `device_seckey.h`/`device_pubkey.h`/`encrypted.h` are
  overwritten by every Python run and shared between levels — mixing a
  768 build with 1024-generated headers fails the public-key interop check
  (that's the check doing its job).
- **`MlKemKey` is ~5 KB** on the host build — the sample keeps it `static`,
  the same rule the on-device integration must follow (the ML-DSA
  `.bss`-corruption lesson from `MLDSA_HARDWARE_FIXES.md`).

## Security caveats (prototype!)

Same as the X25519 example: the device private key (seed) is embedded in a
C header — prototype-grade key storage only; a KEM provides no sender
authentication, the SUIT COSE_Sign1 signature remains the authenticity
anchor (sign-then-encrypt / decrypt-then-verify); one encrypted artifact
per device key.

## SUIT integration

`../MLKEM_ENCRYPTION_PLAN.md` tracks the device integration: algorithm
dispatch next to the X25519 path in `sys/suit/encrypt/decrypt.c`, a
`wolfcrypt_mlkem` module, `SUIT_MANIFEST_ENCRYPT_ALGO`, buffer sizing
(+1.2/+1.7 KB), and the samr21-xpro feasibility matrix across all
signing × encryption combinations.
