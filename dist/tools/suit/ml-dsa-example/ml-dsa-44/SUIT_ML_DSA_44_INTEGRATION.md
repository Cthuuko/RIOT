# SUIT ML-DSA-44 integration

How to use ML-DSA-44 (FIPS 204, NIST security category 2, COSE algorithm ID
`-48` per RFC 9964) for SUIT manifest signing and on-device verification in
RIOT. This is written as a **delta** against the working ML-DSA-65
integration — read `../SUIT_ML_DSA_INTEGRATION.md` first; everything there
(architecture, libcose backend, `PKG_SOURCE_LOCAL_WOLFSSL` wiring, the bugs
found) applies here unchanged unless noted.

ML-DSA-44 is the most interesting set for constrained targets: compared to
ML-DSA-65 the public key shrinks 1952 → **1312** bytes, the signature
3309 → **2420** bytes, and wolfCrypt's verify working memory scales down
with the smaller matrix dimensions (k×l = 4×4 vs 6×5). Since ML-DSA-65
squeezed onto `samr21-xpro` (32KB RAM) with only ~216 bytes to spare,
ML-DSA-44 is the set that fits with real headroom.

## What already works (no changes needed)

- **Host tooling**: `suit-tool keygen -t ml-dsa-44` and `suit-tool sign`
  fully support ML-DSA-44 (committed in stage 1; `suit_tool/manifest.py`
  registers `'ML-DSA-44': -48`, `keygen.py`/`sign.py` handle
  `MLDSA44PrivateKey`).
- **Key generation via make**: `SUIT_KEY_ALGO=ml-dsa-44` already reaches
  `openssl genpkey -algorithm ml-dsa-44` in `makefiles/suit.base.inc.mk`,
  and the `seed-only` output-format workaround is applied by the existing
  `$(filter ml-dsa-%,...)` — Python's `cryptography` cannot parse OpenSSL
  3.5's default combined seed+expanded-key form.
- **Pubkey header**: `dist/tools/suit/pubkey_to_header.py` is
  algorithm-agnostic (`public_bytes_raw()`), so 1312-byte keys just work.
- **wolfSSL sources**: the vendored `../wolfssl` checkout implements all
  three parameter sets; nothing to rebuild on the host side (this
  directory's example verifies exactly that).

## What must change for on-device verification

### 1. `pkg/wolfssl/include/user_settings.h` — stop compiling ML-DSA-44 out

The ML-DSA-65 integration deliberately minimized code size with:

```c
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_87
```

For an ML-DSA-44 build this must become "44 only":

```c
#ifdef MODULE_WOLFCRYPT_MLDSA
...
/* keep only the parameter set actually used; guard per suit_algo module */
#if IS_USED(MODULE_SUIT_ALGO_MLDSA44)
#define WOLFSSL_NO_ML_DSA_65
#define WOLFSSL_NO_ML_DSA_87
#else
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_87
#endif
#endif
```

Keep `WOLFSSL_MLDSA_VERIFY_ONLY`, `WOLFSSL_MLDSA_VERIFY_SMALL_MEM`,
`WOLFSSL_MLDSA_VERIFY_NO_MALLOC`, and `WOLFSSL_MLDSA_ASSIGN_KEY` — the
verify-only/small-mem/no-malloc combination is what made the M0+ build
work, and `ASSIGN_KEY` (public key stays a flash pointer instead of being
copied into the key struct) saves another ~1.3KB of RAM here.

### 2. `pkg/libcose` patch — add the `-48` algorithm

Extend `patches/0002-cose-crypto-add-mldsa65-algorithm.patch` (or add a
sibling patch):

```c
/* include/cose_defines.h */
COSE_ALGO_MLDSA44 = -48,

/* include/cose/crypto/keysizes.h */
#define COSE_CRYPTO_SIGN_MLDSA44_PUBLICKEYBYTES         1312U
#define COSE_CRYPTO_SIGN_MLDSA44_SECRETKEYBYTES         2560U
#define COSE_CRYPTO_SIGN_MLDSA44_SIGNBYTES              2420U
```

and in `src/crypt/wolfcrypt_mldsa.c` either duplicate the verify function
or (better) parameterize the existing one — the only two things that differ
are the wolfCrypt security category and the raw public-key length:

```c
static int _verify_mldsa(int category, word32 pubkey_len,
                         const cose_key_t *key, const uint8_t *sign,
                         size_t signlen, uint8_t *msg, uint64_t msglen)
{
    /* NOTE: static, NOT a stack local — see "learnings" below */
    static MlDsaKey mldsa_key;
    ...
    ret = wc_MlDsaKey_SetParams(&mldsa_key, category);   /* 2 = ML-DSA-44 */
    ret = wc_MlDsaKey_ImportPubRaw(&mldsa_key, key->x, pubkey_len);
    /* VerifyCtx with an empty (NULL, 0) context, NOT plain Verify -
     * required for interop with Python `cryptography` signatures */
    ret = wc_MlDsaKey_VerifyCtx(&mldsa_key, sign, signlen, NULL, 0,
                                msg, msglen, &verify_result);
    ...
}
```

Also add the `COSE_ALGO_MLDSA44` dispatch case in `src/cose_crypto.c`
(verify switch **and** the signature-size switch, returning
`COSE_CRYPTO_SIGN_MLDSA44_SIGNBYTES`).

### 3. `sys/suit/` — new pseudomodule + key selection

- `makefiles/pseudomodules.inc.mk`: already covered by the existing
  `PSEUDOMODULES += suit_algo_%` wildcard — nothing to do.
- `sys/suit/Makefile.dep`: mirror the mldsa65 clause:

  ```make
  ifneq (,$(filter suit_algo_mldsa44,$(USEMODULE)))
    USEMODULE += libcose_crypt_wolfcrypt_mldsa
  endif
  ```

- `sys/suit/handlers_envelope.c` (`suit_get_public_key()`): add a
  `#if IS_USED(MODULE_SUIT_ALGO_MLDSA44)` branch passing
  `COSE_ALGO_MLDSA44` to `cose_key_set_keys()`.

### 4. Makefiles — the knob

- App Makefile (`examples/advanced/suit_update/Makefile`), **before**
  `include $(RIOTBASE)/Makefile.include`:

  ```make
  ifeq (ml-dsa-44,$(SUIT_KEY_ALGO))
    USEMODULE += suit_algo_mldsa44
  endif
  ```

  Reminder from the 65 integration: adding this in
  `makefiles/suit.base.inc.mk` instead silently does nothing (dependency
  resolution has already run) and produces a false-positive build with no
  ML-DSA symbols — always confirm with
  `nm bin/<board>/*.elf | grep MlDsa`.

- `makefiles/suit.base.inc.mk`: widen the local-wolfSSL gate from
  `ifeq (ml-dsa-65,$(SUIT_KEY_ALGO))` to
  `ifneq (,$(filter ml-dsa-%,$(SUIT_KEY_ALGO)))` so
  `PKG_SOURCE_LOCAL_WOLFSSL` points at `../wolfssl` for every ML-DSA level.

### 5. App-level sizing (`samr21-xpro` learnings, scaled to 44)

The three hardware fixes from the ML-DSA-65 bring-up
(`examples/advanced/suit_update/MLDSA_HARDWARE_FIXES.md`) map to ML-DSA-44
as follows:

1. **`SUIT_MANIFEST_BUFSIZE`** — the default 640B still can't hold an
   ML-DSA manifest. The 65 build needed 3840 for a 3309B signature
   (~530B of manifest around it); a 2420B ML-DSA-44 signature fits in
   **3072** with similar margin. Set in the app Makefile:
   `CFLAGS += -DSUIT_MANIFEST_BUFSIZE=3072` (saves 768B RAM vs the 65
   setting).
2. **Never put `MlDsaKey` on a thread stack.** With
   `WOLFSSL_MLDSA_VERIFY_NO_MALLOC` the struct carries its working buffers
   inline — 12.4KB for ML-DSA-65, and still on the order of ~8–9KB for
   ML-DSA-44 (check `sizeof(MlDsaKey)` for the exact figure in your
   config). That overflows the 12KB SUIT worker stack silently on
   Cortex-M0+ (no MPU: valid signatures then fail with `SIG_VERIFY_E` due
   to `.bss` corruption). Keep the `static MlDsaKey` from
   `pkg/libcose/patches/0002-*.patch`.
3. **RAM budget** — with `WOLFSSL_MLDSA_ASSIGN_KEY` and
   `SUIT_WORKER_STACKSIZE=4096` the 65 build landed at 32,552/32,768
   bytes. ML-DSA-44 shaves ≥5KB off that (smaller static key struct,
   smaller manifest buffer), so it should fit `samr21-xpro` with genuine
   headroom rather than 216 bytes.

## Suggested verification sequence

Repeat the 65 methodology:

1. Host interop first (this directory): `python3 test_ml_dsa_key.py` +
   `./mldsa44-example` → both `VALID`.
2. `SUIT_KEY_ALGO=ml-dsa-44 make -C examples/advanced/suit_update
   BOARD=native64 all` — then `nm` the `.elf` for `wc_MlDsaKey_ImportPubRaw`
   / `cose_crypto_verify_mldsa*` before trusting the green build.
3. Positive + negative verify of a real `suit-tool sign`ed manifest on
   `native64` (wrong-key must fail with `COSE_ERR_CRYPTO`).
4. Ed25519 regression build (byte-identical when ML-DSA is off).
5. Real-hardware pass on `samr21-xpro`. Board gotchas from the 65 session:
   EDBG serial is `/dev/ttyACM1`, flash with the ethos terminal closed,
   and `make suit/notify` exiting 143 on the host is normal — the device
   still triggers.
