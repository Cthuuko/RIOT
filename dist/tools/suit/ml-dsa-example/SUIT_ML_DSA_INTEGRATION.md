# ML-DSA support for SUIT: implementation summary

This documents how ML-DSA-65 (FIPS 204 post-quantum signatures) was added
as an alternative to Ed25519 for RIOT's SUIT firmware-update manifests, in
two stages: signing tooling (already committed, `00e23c366a`), and on-device
verification (this document, currently staged/uncommitted).

One knob controls both: `SUIT_KEY_ALGO=ml-dsa-65`.

```
SUIT_KEY_ALGO=ml-dsa-65 make -C examples/advanced/suit_update BOARD=native64 all
```

## Stage 1 — Manifest signing (tooling only, already committed)

Goal: generate ML-DSA-65 keys and sign SUIT manifests with them from the
host-side Python tooling, without touching firmware. IANA's COSE algorithm
IDs for ML-DSA come from RFC 9964: `ML-DSA-44 = -48`, `ML-DSA-65 = -49`,
`ML-DSA-87 = -50`.

### `suit_tool/manifest.py` — register the COSE algorithm IDs

```python
class COSE_Algorithms(SUITKeyMap):
    rkeymap, keymap = SUITKeyMap.mkKeyMaps({
        'ES256' : -7,
        'ES384' : -35,
        'ES512' : -36,
        'EdDSA' : -8,
        'HSS-LMS' : -46,
        'ML-DSA-44' : -48,
        'ML-DSA-65' : -49,
        'ML-DSA-87' : -50,
    })
```

### `suit_tool/keygen.py` — `suit-tool keygen -t ml-dsa-65`

```python
from cryptography.hazmat.primitives.asymmetric import mldsa
...
KeyGenerators = {
    'secp256r1' : lambda o: ec.generate_private_key(ec.SECP256R1(), default_backend()),
    'secp384r1' : lambda o: ec.generate_private_key(ec.SECP384R1(), default_backend()),
    'secp521r1' : lambda o: ec.generate_private_key(ec.SECP521R1(), default_backend()),
    'ed25519' : lambda o: ed25519.Ed25519PrivateKey.generate(),
    'ml-dsa-44' : lambda o: mldsa.MLDSA44PrivateKey.generate(),
    'ml-dsa-65' : lambda o: mldsa.MLDSA65PrivateKey.generate(),
    'ml-dsa-87' : lambda o: mldsa.MLDSA87PrivateKey.generate(),
}
```

### `suit_tool/sign.py` — key-type detection + signing

```python
from cryptography.hazmat.primitives.asymmetric import mldsa
...
elif isinstance(private_key, mldsa.MLDSA44PrivateKey):
    options.key_type = 'ML-DSA-44'
elif isinstance(private_key, mldsa.MLDSA65PrivateKey):
    options.key_type = 'ML-DSA-65'
elif isinstance(private_key, mldsa.MLDSA87PrivateKey):
    options.key_type = 'ML-DSA-87'
...
digest = {
    ...
    'ML-DSA-44' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
    'ML-DSA-65' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
    'ML-DSA-87' : hashes.Hash(hashes.SHA256(), backend=default_backend()),
}.get(options.key_type)
...
signature_bytes = {
    'EdDSA' : get_cose_raw_sign_bytes,   # renamed from get_cose_ed25519_bytes,
    'HSS-LMS' : get_hsslms_bytes,        # body (private_key.sign(sig_val)) is
    'ML-DSA-44' : get_cose_raw_sign_bytes,  # already algorithm-agnostic
    'ML-DSA-65' : get_cose_raw_sign_bytes,
    'ML-DSA-87' : get_cose_raw_sign_bytes,
}.get(options.key_type)(options, private_key, Sig_structure)
```

The digest here is only the manifest-payload hash (SHA-256, independent of
the signing algorithm's own internal hashing) — the same pattern EdDSA
already used, so ML-DSA reuses it unchanged.

### `makefiles/suit.base.inc.mk` — key generation, generalized

```make
SUIT_KEY_ALGO ?= ed25519
# OpenSSL 3.5+ defaults to writing ML-DSA private keys with both the seed
# and the expanded secret key combined. Python's `cryptography` can't parse
# that - force seed-only output, which it can.
SUIT_KEY_GENPKEY_ARGS ?= $(if $(filter ml-dsa-%,$(SUIT_KEY_ALGO)),-provparam ml-dsa.output_formats=seed-only)
...
openssl genpkey -algorithm $(SUIT_KEY_ALGO) $(SUIT_KEY_GENPKEY_ARGS) -out $@
...
# generic (was `openssl ec`, EC/Ed25519-only)
openssl pkey -inform pem -in $< -outform pem -pubout -out $@
```

### `dist/tools/suit/pubkey_to_header.py` — new, algorithm-agnostic pubkey → C header

```python
def main():
    raw_keys = []
    for path in sys.argv[1:]:
        with open(path, 'rb') as f:
            pubkey = load_pem_public_key(f.read())
        raw_keys.append(pubkey.public_bytes_raw())
    key_size = len(raw_keys[0])
    ...
    print(f"const uint8_t public_key[][{key_size}] = {{")
```

Replaces the old `openssl ec ... | tail -c 32 | xxd -i` pipeline, which
hardcoded Ed25519's 32-byte key size via a DER byte offset. `public_bytes_raw()`
works generically for any algorithm `cryptography` supports.

**Bug found and fixed in this stage**: OpenSSL 3.5's default `genpkey`
output for ML-DSA combines the 32-byte seed *and* the ~4KB expanded secret
key in one ASN.1 `CHOICE`. Python's `cryptography` library can't parse that
combined form — only the "seed-only" one. Confirmed empirically:

```
$ openssl genpkey -algorithm ml-dsa-65 -out key.pem            # default: "both"
$ python3 -c "...load_pem_private_key(open('key.pem','rb').read())..."
ValueError: ... unexpected tag (got Tag { value: 16, ... })     # FAILS

$ openssl genpkey -algorithm ml-dsa-65 \
    -provparam ml-dsa.output_formats=seed-only -out key.pem
$ python3 -c "...load_pem_private_key(...)..."
OK <class '...mldsa.MLDSA65PrivateKey'>                         # WORKS
```

## Stage 2 — On-device verification (this session)

Goal: make `sys/suit/handlers_envelope.c` actually verify an ML-DSA-65
signature on target, using a self-built wolfSSL (the checkout already in
this directory, `wolfssl/`, built with `--enable-dilithium`) rather than
RIOT's own pinned `pkg/wolfssl` (too old to have ML-DSA support at all).

### Architecture

```
suit_get_public_key()  →  cose_key_set_keys(..., COSE_ALGO_MLDSA65, ...)
        ↓ (sys/suit/handlers_envelope.c)
cose_sign_verify()      →  cose_crypto_verify()  →  cose_crypto_verify_mldsa65()
        ↓ (pkg/libcose, patched)                        ↓ (new backend)
                                                    wc_MlDsaKey_VerifyCtx()
                                                    (pkg/wolfssl, ML-DSA-65 module,
                                                     sourced from the local wolfssl/
                                                     checkout via PKG_SOURCE_LOCAL_WOLFSSL)
```

Investigation established two things that simplified the change:
- `SUIT_COSE_BUF_SIZE` (`sys/include/suit.h`, 180 bytes) only ever holds the
  small reconstructed COSE `Sig_structure` (protected header + digest
  payload, tens of bytes) — **not** the signature or key, so it needed no
  resizing despite ML-DSA-65's much bigger keys/signatures.
- `cose_key_t` (`pkg/libcose`) stores key material as `uint8_t *` pointers,
  not fixed-size arrays, so a 1952-byte ML-DSA-65 public key drops in with
  no struct changes.

### `sys/suit/handlers_envelope.c` — pick the algorithm

```c
bool suit_get_public_key(uint8_t idx, cose_key_t *pkey)
{
    if (idx >= ARRAY_SIZE(public_key)) {
        return false;
    }
    cose_key_init(pkey);
#if IS_USED(MODULE_SUIT_ALGO_MLDSA65)
    /* curve argument is unused for non-EC key types, see cose_key_set_keys() */
    cose_key_set_keys(pkey, COSE_EC_CURVE_ED25519, COSE_ALGO_MLDSA65,
                      (void *)public_key[idx], NULL, NULL);
#else
    cose_key_set_keys(pkey, COSE_EC_CURVE_ED25519, COSE_ALGO_EDDSA,
                      (void *)public_key[idx], NULL, NULL);
#endif
    return true;
}
```

`_verify_with_key()`/`cose_sign_verify()` below this were **not** touched —
they already dispatch on `pkey->algo`, algorithm-agnostic.

### `sys/suit/Makefile.dep` — select the crypto backend

```make
ifneq (,$(filter suit_algo_mldsa65,$(USEMODULE)))
  USEMODULE += libcose_crypt_wolfcrypt_mldsa
endif

ifeq (,$(filter libcose_crypt_%,$(USEMODULE)))
  USEMODULE += libcose_crypt_c25519      # unchanged default
endif
```

### `makefiles/pseudomodules.inc.mk` — declare the new module family

```make
PSEUDOMODULES += suit_transport_%
PSEUDOMODULES += suit_storage_%
PSEUDOMODULES += suit_algo_%
```

### `examples/advanced/suit_update/Makefile` — turn the knob on early

```make
USEMODULE += suit suit_transport_coap
USEMODULE += nanocoap_server_auto_init

# SUIT_KEY_ALGO=ml-dsa-65 selects on-device ML-DSA-65 verification (must be
# added to USEMODULE before dependency resolution runs in Makefile.include,
# so this can't live in the later-included makefiles/suit.base.inc.mk).
ifeq (ml-dsa-65,$(SUIT_KEY_ALGO))
  USEMODULE += suit_algo_mldsa65
endif
```

**Bug found and fixed**: `makefiles/suit.base.inc.mk` (and the
`makefiles/suit.inc.mk` chain it's part of) is `include`d in
`Makefile.include` *after* module dependency resolution has already run —
so `USEMODULE += suit_algo_mldsa65` added there silently had no effect (the
wolfSSL/libcose backend never got pulled in, and the build "succeeded" with
a byte-identical binary to a build with no ML-DSA support at all — a
silent, false-positive success that had to be caught by checking `nm` for
the expected symbols). Fixed by adding the `USEMODULE` line directly to the
app's own Makefile, which runs before `include $(RIOTBASE)/Makefile.include`.

### `makefiles/suit.base.inc.mk` — wire in the local wolfSSL checkout

```make
# ML-DSA-65 needs on-device verification support (sys/suit's
# suit_algo_mldsa65 module, wired to a wolfCrypt-backed libcose backend -
# the app Makefile must add `USEMODULE += suit_algo_mldsa65` itself when
# SUIT_KEY_ALGO=ml-dsa-65, since module selection here runs too late,
# after dependency resolution). The backend sources wolfCrypt's ML-DSA code
# from a local, pre-configured wolfSSL checkout (built with
# --enable-dilithium) rather than RIOT's own pinned wolfssl pkg fetch, which
# predates wolfSSL's ML-DSA support.
ifeq (ml-dsa-65,$(SUIT_KEY_ALGO))
  export PKG_SOURCE_LOCAL_WOLFSSL ?= $(RIOTBASE)/dist/tools/suit/ml-dsa-example/wolfssl
endif
```

`PKG_SOURCE_LOCAL_WOLFSSL` is RIOT's built-in package-override mechanism
(`pkg/pkg.mk` → `pkg/local.mk`): instead of `git clone`-fetching
`pkg/wolfssl`'s pinned commit, it `cp -a`s this local checkout into
`build/pkg/wolfssl` and builds from that.

### `pkg/wolfssl/` — expose ML-DSA-65 as a RIOT module

`pkg/wolfssl/include/user_settings.h`:

```c
#ifdef MODULE_WOLFCRYPT_MLDSA
#define WOLFSSL_HAVE_MLDSA
#define WOLFSSL_MLDSA_NO_CTX
/* ML-DSA needs SHAKE-128/256 (MODULE_WOLFCRYPT_SHA3 above only turns on
 * WOLFSSL_SHA3, not the SHAKE XOF variants dilithium/wc_mldsa need). */
#define WOLFSSL_SHAKE128
#define WOLFSSL_SHAKE256
/* Needed to import a raw public key (wc_MlDsaKey_ImportPubRaw), as done
 * with the compiled-in SUIT trusted public key. */
#define WOLFSSL_MLDSA_PUBLIC_KEY
/* SUIT firmware only ever verifies manifests, never signs on-device -
 * use wolfCrypt's smallest-footprint verify-only build. */
#define WOLFSSL_MLDSA_VERIFY_ONLY
#define WOLFSSL_MLDSA_VERIFY_SMALL_MEM
#define WOLFSSL_MLDSA_VERIFY_NO_MALLOC
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_87
#endif
```

`pkg/wolfssl/Makefile.wolfcrypt`:

```make
ifeq (native,$(CPU))
  CFLAGS += -Wno-array-parameter
  # wc_port.c's wc_accept_cloexec() uses glibc's accept4(), only declared
  # when _GNU_SOURCE is defined (needed once wolfcrypt_asn - pulled in by
  # wolfcrypt_mldsa - is active on native; other wolfcrypt modules don't
  # reach that code path).
  CFLAGS += -D_GNU_SOURCE
endif
...
# wc_mldsa.c is not auto-added by the wolfcrypt_% submodule convention
# (its name doesn't match the "wolfcrypt_mldsa" pseudomodule suffix)
ifneq (,$(filter wolfcrypt_mldsa,$(USEMODULE)))
  SRC += wc_mldsa.c
endif
```

`pkg/wolfssl/Makefile.dep`:

```make
ifneq (,$(filter wolfcrypt_mldsa,$(USEMODULE)))
  USEMODULE += wolfcrypt_sha3
  USEMODULE += wolfcrypt_asn
endif
```

### `pkg/libcose/patches/0002-cose-crypto-add-mldsa65-algorithm.patch` — new algorithm + backend

Applied automatically by RIOT's pkg build (`git am`) on every fetch, same
mechanism as the library's existing patch. Adds:

`include/cose_defines.h`:
```c
COSE_ALGO_MLDSA65 = -49,            /**< ML-DSA-65 (FIPS 204) signature algo */
```

`include/cose/crypto/keysizes.h`:
```c
#define COSE_CRYPTO_SIGN_MLDSA65_PUBLICKEYBYTES         1952U
#define COSE_CRYPTO_SIGN_MLDSA65_SECRETKEYBYTES         4032U
#define COSE_CRYPTO_SIGN_MLDSA65_SIGNBYTES              3309U
```

`src/cose_crypto.c` (the algorithm dispatch table):
```c
#ifdef HAVE_ALGO_MLDSA65
        case COSE_ALGO_MLDSA65:
            return cose_crypto_verify_mldsa65(key, sign, signlen, msg, msglen);
            break;
#endif
...
        case COSE_ALGO_MLDSA65:
            return COSE_CRYPTO_SIGN_MLDSA65_SIGNBYTES;
            break;
```

`src/crypt/wolfcrypt_mldsa.c` (new file, the actual backend):
```c
#include <wolfssl/wolfcrypt/dilithium.h>
#include "cose_defines.h"
#include "cose/crypto.h"
#include "cose/crypto/wolfcrypt_mldsa.h"

#ifdef HAVE_ALGO_MLDSA65
int cose_crypto_verify_mldsa65(const cose_key_t *key, const uint8_t *sign,
                                size_t signlen, uint8_t *msg, uint64_t msglen)
{
    MlDsaKey mldsa_key;
    int verify_result = 0;
    int ret = wc_MlDsaKey_Init(&mldsa_key, NULL, INVALID_DEVID);

    if (ret == 0) {
        ret = wc_MlDsaKey_SetParams(&mldsa_key, 3); /* category 3 = ML-DSA-65 */
    }
    if (ret == 0) {
        ret = wc_MlDsaKey_ImportPubRaw(&mldsa_key, key->x,
                                        COSE_CRYPTO_SIGN_MLDSA65_PUBLICKEYBYTES);
    }
    if (ret == 0) {
        /* VerifyCtx with an empty context matches how Python's `cryptography`
         * library (used by suit-tool sign) signs - plain wc_MlDsaKey_Verify()
         * is not interoperable with it, confirmed against
         * dist/tools/suit/ml-dsa-example/wolfcrypt-sample.c. */
        ret = wc_MlDsaKey_VerifyCtx(&mldsa_key, sign, signlen, NULL, 0,
                                     msg, msglen, &verify_result);
    }
    wc_MlDsaKey_Free(&mldsa_key);
    return (ret == 0 && verify_result == 1) ? 0 : -1;
}
#endif /* HAVE_ALGO_MLDSA65 */
```

**Bug found and fixed**: the first working version of this function called
plain `wc_MlDsaKey_Verify()` (no context) and consistently rejected
genuinely valid, correctly-matched (message, signature, public key) triples
— confirmed byte-for-byte identical between a Python (`cryptography`)
reference reconstruction and the C code's actual inputs, ruling out a data
mismatch. The fix was switching to `wc_MlDsaKey_VerifyCtx()` with an empty
(`NULL, 0`) context — this exact interop gotcha is called out by name in
`wolfcrypt-sample.c`'s own comment ("VerifyCtx with an empty context
matches how the Python `cryptography` library signs here"), which this
change had initially missed.

`pkg/libcose/Makefile.dep` / `Makefile.include` register the new
`libcose_crypt_wolfcrypt_mldsa` pseudomodule (mirroring `libcose_crypt_c25519`
etc.) and its `-DCRYPTO_WOLFCRYPT_MLDSA` selector, and pull in
`USEPKG += wolfssl`, `USEMODULE += wolfcrypt wolfcrypt_mldsa` when active.

### Fix applied directly to the vendored wolfSSL checkout

`dist/tools/suit/ml-dsa-example/wolfssl/wolfcrypt/src/wc_port.c` (this
checkout is what RIOT actually compiles from for ML-DSA, via
`PKG_SOURCE_LOCAL_WOLFSSL`, so a fix here is picked up automatically):

```c
int wc_accept_cloexec(int sockfd, void* addr, void* addrlen)
{
    int fd;
/* RIOT's own posix_sockets <sys/socket.h> shim (sys/posix/include) shadows
 * the real glibc header on native builds and doesn't declare accept4(),
 * even though __USE_GNU is otherwise set - fall back to the portable
 * accept()+fcntl() path below instead. WOLFSSL_RIOT_OS is defined
 * unconditionally by pkg/wolfssl/Makefile.include. */
#if !defined(WOLFSSL_RIOT_OS) && \
    ((defined(__USE_GNU) && (defined(__linux__) || defined(__ANDROID__))) || \
    (defined(__FreeBSD__) && defined(__BSD_VISIBLE) && __BSD_VISIBLE && \
     (__FreeBSD_version >= 1000000)))
    fd = accept4(sockfd, (struct sockaddr*)addr, (socklen_t*)addrlen,
                 SOCK_CLOEXEC);
    ...
```

This is a `native`-target-only build quirk (real embedded boards never
define `__unix__`, so this whole code path is skipped there); harmless
elsewhere.

## Verification performed

1. **Build**: `SUIT_KEY_ALGO=ml-dsa-65 make -C examples/advanced/suit_update
   BOARD=native64 all` compiles and links cleanly; `nm` on the resulting
   `.elf` confirms `wc_MlDsaKey_Verify`, `wc_MlDsaKey_ImportPubRaw`, and
   `cose_crypto_verify_mldsa65` are actually present (not silently dropped).
2. **Real signature, positive path**: generated a payload + manifest,
   signed it with `suit-tool sign` using a real ML-DSA-65 key, extracted the
   exact COSE_Sign1 bytes and compiled-in public key, and called
   `cose_sign_decode()` + `cose_sign_verify()` (the same functions
   `sys/suit/handlers_envelope.c`'s `_verify_with_key()` calls) directly in
   a minimal RIOT native test app → `VALID`.
3. **Real signature, negative path**: same signature, wrong public key →
   correctly rejected (`COSE_ERR_CRYPTO`).
4. **Regression**: repeated the same positive/negative test with the
   default Ed25519 path (`libcose_crypt_c25519`, untouched by this change)
   → still `VALID`/correctly rejected, byte-identical binary size to a
   pre-change build.

## Known limitations

- RAM/flash feasibility on real constrained boards (default
  `samr21-xpro`, 32KB RAM) is **unverified** — only tested on `native64`.
- `pkg/local.mk`'s local-source override (`PKG_SOURCE_LOCAL_WOLFSSL`)
  `cp -a`s the ~1.1GB local wolfSSL checkout into `build/pkg/wolfssl` on
  every clean/prepare (slow), and **skips** `pkg/wolfssl`'s own small
  patch set (TLSX/gettimeofday fixes) entirely — those patches don't touch
  anything the ML-DSA path uses, but it's a real gap if something else in
  that tree needs them later.
- Mixed-algorithm multi-key SUIT builds (some Ed25519, some ML-DSA keys in
  one firmware) are not supported — `dist/tools/suit/pubkey_to_header.py`
  requires all configured `SUIT_KEY` entries to share one key size.
