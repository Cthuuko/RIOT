# ML-DSA multi-level SUIT support (44 / 65 / 87) — code changes

Date: 2026-07-18. This documents the generalization of the SUIT ML-DSA
integration from a hardcoded ML-DSA-65 to a selectable FIPS 204 parameter
set. One knob picks the level end-to-end (key generation, manifest signing,
and on-device verification):

```
SUIT_KEY_ALGO=ml-dsa-44 make -C examples/advanced/suit_update BOARD=native64 all
SUIT_KEY_ALGO=ml-dsa-65 make -C examples/advanced/suit_update BOARD=native64 all
SUIT_KEY_ALGO=ml-dsa-87 make -C examples/advanced/suit_update BOARD=native64 all
```

Background for the original (65-only) integration is in
`dist/tools/suit/ml-dsa-example/SUIT_ML_DSA_INTEGRATION.md`; the per-level
planning notes are in
`dist/tools/suit/ml-dsa-example/ml-dsa-{44,87}/SUIT_ML_DSA_*_INTEGRATION.md`.

## Per-level constants

| | ML-DSA-44 | ML-DSA-65 | ML-DSA-87 |
|---|---|---|---|
| wolfCrypt security category | 2 | 3 | 5 |
| COSE algorithm ID (RFC 9964) | −48 | −49 | −50 |
| Public key | 1312 B | 1952 B | 2592 B |
| Signature | 2420 B | 3309 B | 4627 B |
| `SUIT_MANIFEST_BUFSIZE` (app Makefile) | 3072 | 3840 | 5376 |

Host-side tooling (`suit-tool keygen/sign`, the COSE algorithm IDs in
`suit_tool/manifest.py`, the OpenSSL `seed-only` workaround in
`makefiles/suit.base.inc.mk`, and `dist/tools/suit/pubkey_to_header.py`)
already supported all three sets and needed **no changes**.

## Changes per file

### `pkg/libcose/patches/0002-cose-crypto-add-mldsa-algorithms.patch`
(replaces `0002-cose-crypto-add-mldsa65-algorithm.patch`, regenerated via
`git format-patch` from the pkg checkout)

- `include/cose_defines.h`: adds `COSE_ALGO_MLDSA44 = -48` and
  `COSE_ALGO_MLDSA87 = -50` beside the existing `-49`.
- `include/cose/crypto/keysizes.h`: adds `COSE_CRYPTO_SIGN_MLDSA44_*` and
  `..._MLDSA87_*` size constants (see table).
- `include/cose/crypto/wolfcrypt_mldsa.h`: now includes
  `<wolfssl/wolfcrypt/settings.h>` and derives `HAVE_ALGO_MLDSA44/65/87`
  from wolfSSL's own `WOLFSSL_NO_ML_DSA_xx` compile-out switches — libcose
  only advertises the parameter set(s) the wolfCrypt build actually
  contains.
- `include/cose/crypto.h`: declares `cose_crypto_verify_mldsa44/87` next to
  the existing 65 declaration.
- `src/crypt/wolfcrypt_mldsa.c`: refactored to one internal
  `_verify_mldsa(category, pubkey_len, ...)` plus three thin per-level
  wrappers. Unchanged learnings baked in: the work state stays in a
  `static MlDsaKey` (never a stack local — silent stack overflow on M0+),
  and verification uses `wc_MlDsaKey_VerifyCtx(..., NULL, 0, ...)` with an
  empty context (plain `wc_MlDsaKey_Verify()` is not interoperable with
  signatures from Python's `cryptography`, which `suit-tool sign` uses).
- `src/cose_crypto.c`: `COSE_ALGO_MLDSA44/87` cases added to the verify
  dispatch and the signature-size switch.

### `pkg/wolfssl/include/user_settings.h`

The ML-DSA block no longer hardcodes `WOLFSSL_NO_ML_DSA_44/87`. Each set is
compiled out unless its pseudomodule is selected:

```c
#ifndef MODULE_WOLFCRYPT_MLDSA44
#define WOLFSSL_NO_ML_DSA_44
#endif
/* likewise for 65 and 87 */
```

The new `wolfcrypt_mldsa44/65/87` pseudomodules need no declaration —
`pkg/wolfssl/Makefile.include` already has `PSEUDOMODULES += wolfcrypt_%`.
All footprint switches (`WOLFSSL_MLDSA_VERIFY_ONLY/_SMALL_MEM/_NO_MALLOC`,
`WOLFSSL_MLDSA_ASSIGN_KEY`) are kept for every level.

### `sys/suit/Makefile.dep`

The `suit_algo_mldsa65` filter became `suit_algo_mldsa%`; it selects the
shared `libcose_crypt_wolfcrypt_mldsa` backend and maps each
`suit_algo_mldsaXX` to its `wolfcrypt_mldsaXX` pseudomodule via `patsubst`.

### `sys/suit/handlers_envelope.c`

`suit_get_public_key()` extended from an `MLDSA65/else` pair to a
44 → 65 → 87 → Ed25519 `#if IS_USED(...)` chain passing the matching
`COSE_ALGO_MLDSAxx` to `cose_key_set_keys()`.

### `examples/advanced/suit_update/Makefile`

The `ifeq (ml-dsa-65,...)` block became `$(filter ml-dsa-%,...)`:
`USEMODULE += suit_algo_mldsa$(subst ml-dsa-,,$(SUIT_KEY_ALGO))` (still
**before** `Makefile.include` — adding it later silently does nothing,
because dependency resolution has already run), a per-level
`SUIT_MANIFEST_BUFSIZE` (table above), and `SUIT_WORKER_STACKSIZE=4096`
for all levels.

### `makefiles/suit.base.inc.mk`

The `PKG_SOURCE_LOCAL_WOLFSSL` gate widened from `ifeq (ml-dsa-65,...)` to
`$(filter ml-dsa-%,...)` — the vendored checkout at
`dist/tools/suit/ml-dsa-example/wolfssl` (built with `--enable-dilithium`)
implements all three parameter sets.

## Testing performed (BOARD=native64 only)

Setup: dedicated keys per level
(`openssl genpkey -algorithm ml-dsa-XX -provparam
ml-dsa.output_formats=seed-only`, stored as
`~/.local/share/RIOT/keys/mldsaXX.pem`), manifests generated with
`SUIT_COAP_ROOT=file:///nvm0 make ... suit/manifest` and placed with their
payload in the app's `native/` folder (exported to RIOT as `/nvm0`), then
fetched on the running ELF with `suit fetch file:///nvm0/<manifest>` — no
networking needed.

| Check | 44 | 65 | 87 |
|---|---|---|---|
| Build (`clean all`) | ✓ | ✓ | ✓ |
| `nm`: only that level's `cose_crypto_verify_mldsaXX` present | ✓ | ✓ | ✓ |
| Correct raw pubkey size embedded in `public_key.h` | ✓ (1312) | ✓ (1952) | ✓ (2592) |
| On-device **signature verification** of a real `suit-tool`-signed manifest | ✓ | ✓ | ✓ |
| Negative: same manifest signed with a wrong key → `Unable to validate signature` (SUIT_ERR_SIGNATURE) | — | — | ✓ |

Regression: the default Ed25519 build ran the **full** update successfully
(`suit_worker: update successful`), unchanged.

### Known issue (pre-existing, not investigated further by request)

With this branch's thesis manifest layout (`coaproot/payload.bin:0:ram:0`
via `Makefile.thesis.suit.manifest`), all three ML-DSA levels fail *after*
successful signature verification, at the component/common-sequence stage
(`Formatted component name: .ram.0` → `suit_parse() failed` with res=-1/-2),
while the identically-generated Ed25519 manifest completes the full update.
A baseline test with all multi-level changes stashed showed the **original
65-only code fails identically**, so this is not a regression from this
change. Left as-is since signature verification (the point of the ML-DSA
integration) succeeds.

### Not covered

- Real hardware (`samr21-xpro`) — explicitly out of scope here; see
  `MLDSA_HARDWARE_FIXES.md` for the 65 bring-up and
  `dist/tools/suit/ml-dsa-example/ml-dsa-{44,87}/SUIT_ML_DSA_*_INTEGRATION.md`
  for per-level RAM budgets (44 should fit with headroom; 87 cannot fit
  32KB).
- CoAP-transport E2E: `coaproot/` and `aiocoap-fileserver` are present, but
  the tap bridge was down and needs sudo (`dist/tools/tapsetup/tapsetup`);
  the `file://` transport exercises the same verification code path.
