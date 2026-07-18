# SUIT ML-DSA-87 integration

How to use ML-DSA-87 (FIPS 204, NIST security category 5, COSE algorithm ID
`-50` per RFC 9964) for SUIT manifest signing and on-device verification in
RIOT. This is written as a **delta** against the working ML-DSA-65
integration — read `../SUIT_ML_DSA_INTEGRATION.md` first; everything there
(architecture, libcose backend, `PKG_SOURCE_LOCAL_WOLFSSL` wiring, the bugs
found) applies here unchanged unless noted.

ML-DSA-87 is the largest parameter set: public key 1952 → **2592** bytes,
signature 3309 → **4627** bytes vs ML-DSA-65, and verify working memory
grows with the k×l = 8×7 matrix (vs 6×5). **Realistic target expectation:**
ML-DSA-65 fit `samr21-xpro`'s 32KB RAM with only ~216 bytes to spare, so
ML-DSA-87 — several KB heavier in every dimension — is out of reach there.
Plan for `native64` and larger boards (e.g. `nrf52840dk`, 256KB RAM).

## What already works (no changes needed)

- **Host tooling**: `suit-tool keygen -t ml-dsa-87` and `suit-tool sign`
  fully support ML-DSA-87 (committed in stage 1; `suit_tool/manifest.py`
  registers `'ML-DSA-87': -50`, `keygen.py`/`sign.py` handle
  `MLDSA87PrivateKey`).
- **Key generation via make**: `SUIT_KEY_ALGO=ml-dsa-87` already reaches
  `openssl genpkey -algorithm ml-dsa-87` in `makefiles/suit.base.inc.mk`,
  and the `seed-only` output-format workaround is applied by the existing
  `$(filter ml-dsa-%,...)` — Python's `cryptography` cannot parse OpenSSL
  3.5's default combined seed+expanded-key form.
- **Pubkey header**: `dist/tools/suit/pubkey_to_header.py` is
  algorithm-agnostic (`public_bytes_raw()`), so 2592-byte keys just work.
- **wolfSSL sources**: the vendored `../wolfssl` checkout implements all
  three parameter sets; nothing to rebuild on the host side (this
  directory's example verifies exactly that).

## What must change for on-device verification

### 1. `pkg/wolfssl/include/user_settings.h` — stop compiling ML-DSA-87 out

The ML-DSA-65 integration deliberately minimized code size with:

```c
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_87
```

For an ML-DSA-87 build this must become "87 only":

```c
#ifdef MODULE_WOLFCRYPT_MLDSA
...
#if IS_USED(MODULE_SUIT_ALGO_MLDSA87)
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_65
#else
#define WOLFSSL_NO_ML_DSA_44
#define WOLFSSL_NO_ML_DSA_87
#endif
#endif
```

Keep `WOLFSSL_MLDSA_VERIFY_ONLY`, `WOLFSSL_MLDSA_VERIFY_SMALL_MEM`,
`WOLFSSL_MLDSA_VERIFY_NO_MALLOC`, and `WOLFSSL_MLDSA_ASSIGN_KEY` — with
`ASSIGN_KEY` the 2592-byte public key stays a flash pointer instead of
being copied into RAM, which matters even more at this size.

### 2. `pkg/libcose` patch — add the `-50` algorithm

Extend `patches/0002-cose-crypto-add-mldsa65-algorithm.patch` (or add a
sibling patch):

```c
/* include/cose_defines.h */
COSE_ALGO_MLDSA87 = -50,

/* include/cose/crypto/keysizes.h */
#define COSE_CRYPTO_SIGN_MLDSA87_PUBLICKEYBYTES         2592U
#define COSE_CRYPTO_SIGN_MLDSA87_SECRETKEYBYTES         4896U
#define COSE_CRYPTO_SIGN_MLDSA87_SIGNBYTES              4627U
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
    ret = wc_MlDsaKey_SetParams(&mldsa_key, category);   /* 5 = ML-DSA-87 */
    ret = wc_MlDsaKey_ImportPubRaw(&mldsa_key, key->x, pubkey_len);
    /* VerifyCtx with an empty (NULL, 0) context, NOT plain Verify -
     * required for interop with Python `cryptography` signatures */
    ret = wc_MlDsaKey_VerifyCtx(&mldsa_key, sign, signlen, NULL, 0,
                                msg, msglen, &verify_result);
    ...
}
```

Also add the `COSE_ALGO_MLDSA87` dispatch case in `src/cose_crypto.c`
(verify switch **and** the signature-size switch, returning
`COSE_CRYPTO_SIGN_MLDSA87_SIGNBYTES`).

### 3. `sys/suit/` — new pseudomodule + key selection

- `makefiles/pseudomodules.inc.mk`: already covered by the existing
  `PSEUDOMODULES += suit_algo_%` wildcard — nothing to do.
- `sys/suit/Makefile.dep`: mirror the mldsa65 clause:

  ```make
  ifneq (,$(filter suit_algo_mldsa87,$(USEMODULE)))
    USEMODULE += libcose_crypt_wolfcrypt_mldsa
  endif
  ```

- `sys/suit/handlers_envelope.c` (`suit_get_public_key()`): add a
  `#if IS_USED(MODULE_SUIT_ALGO_MLDSA87)` branch passing
  `COSE_ALGO_MLDSA87` to `cose_key_set_keys()`.

### 4. Makefiles — the knob

- App Makefile (`examples/advanced/suit_update/Makefile`), **before**
  `include $(RIOTBASE)/Makefile.include`:

  ```make
  ifeq (ml-dsa-87,$(SUIT_KEY_ALGO))
    USEMODULE += suit_algo_mldsa87
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

### 5. App-level sizing (learnings from the `samr21-xpro` bring-up, scaled up)

The three hardware fixes from the ML-DSA-65 bring-up
(`examples/advanced/suit_update/MLDSA_HARDWARE_FIXES.md`) map to ML-DSA-87
as follows:

1. **`SUIT_MANIFEST_BUFSIZE`** — the 65 build needed 3840 for a 3309B
   signature (~530B of manifest around it). A 4627B ML-DSA-87 signature
   needs **5376** with similar margin:
   `CFLAGS += -DSUIT_MANIFEST_BUFSIZE=5376` in the app Makefile. Too-small
   values fail cleanly (manifest rejected as oversized), but only after
   the whole download — size it before testing OTA.
2. **Never put `MlDsaKey` on a thread stack.** With
   `WOLFSSL_MLDSA_VERIFY_NO_MALLOC` the struct carries its working buffers
   inline — 12.4KB for ML-DSA-65 and substantially more for ML-DSA-87's
   8×7 matrix (check `sizeof(MlDsaKey)`; expect roughly 17–20KB). On the
   65 bring-up a stack-allocated key silently corrupted `.bss` on
   Cortex-M0+ (no MPU) and made valid signatures fail with
   `SIG_VERIFY_E`; keep the `static MlDsaKey` from
   `pkg/libcose/patches/0002-*.patch`. Note the static key alone may
   exceed half the RAM of a 32KB board — see the budget below.
3. **RAM budget** — the 65 build ended at 32,552/32,768 bytes on
   `samr21-xpro` after every trick available (`ASSIGN_KEY`,
   `SUIT_WORKER_STACKSIZE=4096`, verify-only small-mem build). ML-DSA-87
   adds ≥7KB on top (bigger static key struct, +1536B manifest buffer,
   bigger signature in flight), so **it cannot fit that board**. Target
   boards with ≥64KB RAM; on `native64` there is no constraint.

## Suggested verification sequence

Repeat the 65 methodology:

1. Host interop first (this directory): `python3 test_ml_dsa_key.py` +
   `./mldsa87-example` → both `VALID`.
2. `SUIT_KEY_ALGO=ml-dsa-87 make -C examples/advanced/suit_update
   BOARD=native64 all` — then `nm` the `.elf` for `wc_MlDsaKey_ImportPubRaw`
   / `cose_crypto_verify_mldsa*` before trusting the green build.
3. Positive + negative verify of a real `suit-tool sign`ed manifest on
   `native64` (wrong-key must fail with `COSE_ERR_CRYPTO`).
4. Ed25519 regression build (byte-identical when ML-DSA is off).
5. Hardware pass on a ≥64KB-RAM board (e.g. `nrf52840dk`) — not
   `samr21-xpro`, per the RAM budget above.
