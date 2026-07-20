# ML-KEM manifest encryption — code changes summary

Companion to `MLKEM_ENCRYPTION_PLAN.md` (plan + status checklist), extending
the X25519 manifest-encryption feature (`MANIFEST_ENCRYPTION_CHANGES.md`)
with a **post-quantum key-establishment alternative**: ML-KEM-768/1024
(FIPS 203), selectable via `SUIT_MANIFEST_ENCRYPT_ALGO` (X25519 stays the
default). Status: verified end-to-end on `native64` (Ed25519 signing +
ML-KEM-768 encryption). samr21-xpro: hardware bring-up (2026-07-20)
uncovered that **every prior samr21 feasibility number in this document
was wrong** (see the correction below) — corrected numbers are in
`FIRMWARE_ENCRYPTION_CHANGES.md`'s feasibility table, which now supersedes
the matrix that used to be here.
`mlkem-encryption-implementation.patch` in this directory holds the diff
of whatever ML-KEM work is not yet committed at regeneration time (same
convention as `manifest-encryption-implementation.patch` for the X25519
feature); currently that is the RAM-rework delta (pq_scratch union, exact
buffer, doc updates).

## ⚠️ Correction (2026-07-20): the original samr21 feasibility matrix below is wrong

Hardware bring-up of the full-PQ combo (ML-DSA-44 + ML-KEM-768) failed
with `suit: manifest CEK derivation failed: -125` (wolfCrypt `MEMORY_E`)
despite the matrix below claiming "1,160 B spare" for exactly that combo.
Root cause: **wolfCrypt's `wc_mlkem.c` heap-allocates its scratch buffers
at runtime** (`XMALLOC`, up to 6,144 B in a single call inside
`wc_MlKemKey_MakeKeyWithRandom`, more inside `Decapsulate`) — this is
completely invisible to `arm-none-eabi-size`/`nm`, which only report
static `.data`/`.bss`. Every number in the table below was link-time-only
and never accounted for it; on real hardware, the newlib heap (a few
hundred bytes to a few KB depending on other tuning) can never satisfy a
single 6 KB allocation, so decryption fails outright with `MEMORY_E`.

Fix (in `pkg/wolfssl/include/user_settings.h`, `MODULE_WOLFCRYPT_MLKEM`
block): `WOLFSSL_NO_MALLOC` + `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` +
`WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM` move those buffers from heap to
**stack** (bounded and measurable, unlike heap). `-fstack-usage` then
gave the real number: `wc_MlKemKey_Decapsulate`'s call chain
(`mlkemkey_decapsulate` → `mlkemkey_encapsulate`, invoked internally for
the FIPS 203 implicit-rejection check) peaks at **~8.5–9 KB of stack** —
roughly double the 4 KB `SUIT_WORKER_STACKSIZE` this feature was
previously built with. That means the ORIGINAL "links successfully" runs
in the table below didn't just have wrong RAM numbers — on real hardware
(no MPU on Cortex-M0+) they would have **silently corrupted adjacent
`.bss`** on every decapsulation, a strictly worse failure mode than the
clean `MEMORY_E` we actually hit. The app Makefile now forces a
correctly-sized 9,216 B worker stack whenever `SUIT_MANIFEST_ENCRYPT_ALGO=
ml-kem-%` is selected on a non-native board, which makes infeasible
combos fail loudly at **link time** (`.bss` overflow) instead.

Re-measured with the fix: the full-PQ combo (any ML-DSA level +
ML-KEM-768/1024) no longer fits at all (❌, off by ~3.3–3.6 KB even
manifest-only); Ed25519 + ML-KEM-768/1024 still fits, with roughly
2–3.4 KB less spare than previously claimed. See
`FIRMWARE_ENCRYPTION_CHANGES.md` for the authoritative re-measured table
(that document's samr21 section was written after this correction).

## What changes vs. the X25519 variant

Only the recipient of the COSE_Encrypt container: instead of an ephemeral
X25519 public key in the recipient's unprotected header, the **ML-KEM
encapsulation ciphertext rides in the recipient's ciphertext field**
(1088 B for 768, 1568 B for 1024). KDF (HKDF-SHA256 + COSE_KDF_Context),
AEAD (ChaCha20-Poly1305), AAD rules, sign-then-encrypt composition, and the
`SUIT_MANIFEST_ENCRYPT` opt-out are all unchanged. Recipient alg IDs are
private-use (**-70768** / **-70769** — IANA has no COSE alg for ML-KEM
yet). The embedded device key is the **64-byte FIPS 203 seed (d‖z)**,
re-expanded on-device by `wc_MlKemKey_MakeKeyWithRandom()` — the same
expansion `cryptography`'s `from_seed_bytes()` performs (proven by the
standalone example's public-key comparison).

## Selection contract

| `SUIT_MANIFEST_ENCRYPT_ALGO` | Device modules | Device key | Buffer pad |
|---|---|---|---|
| `x25519` (default) | c25519 pkg (already present) | `device_x25519.pem` (32 B raw) | +128 B |
| `ml-kem-768` | `wolfcrypt_mlkem` + `wolfcrypt_mlkem768` | `device_mlkem768.pem` (64 B seed) | +1216 B |
| `ml-kem-1024` | `wolfcrypt_mlkem` + `wolfcrypt_mlkem1024` | `device_mlkem1024.pem` (64 B seed) | +1696 B |

Dispatch is **compile-time** (a firmware accepts exactly one scheme — it
only has one device key); the received recipient alg ID is validated, so a
container for the wrong scheme fails fast with
`suit: recipient alg X != built-in Y` instead of a garbage decrypt.
Plaintext pass-through and `SUIT_MANIFEST_ENCRYPT=0` behave exactly as in
the X25519 feature. ML-KEM key generation needs OpenSSL 3.5+
(`-provparam ml-kem.output_formats=seed-only`, the same seed-only lesson as
ML-DSA), and the device build sources wolfSSL from the local
`dist/tools/suit/ml-dsa-example/wolfssl` checkout (the pinned pkg predates
ML-KEM), triggered automatically by `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-%`.

## Changed / added files

| File | Change |
|---|---|
| `sys/suit/encrypt/decrypt.c` | Compile-time KEM dispatch (`#ifdef MODULE_WOLFCRYPT_MLKEM`): recipient-alg validation helper, KEM-ciphertext recipient parsing, `_derive_cek` via `wc_MlKemKey_MakeKeyWithRandom` + `wc_MlKemKey_Decapsulate` (static `MlKemKey` — multi-KB, never on the 4 KB worker stack) |
| `pkg/wolfssl/Makefile.wolfcrypt` | `wolfcrypt_mlkem` module → `wc_mlkem.c` + `wc_mlkem_poly.c` (the `wc_mldsa.c` special-case pattern) |
| `pkg/wolfssl/Makefile.dep` | `wolfcrypt_mlkem` → `wolfcrypt_sha3` |
| `pkg/wolfssl/include/user_settings.h` | `MODULE_WOLFCRYPT_MLKEM` block: `WOLFSSL_HAVE_MLKEM`, SHAKE128/256, `WOLFSSL_MLKEM_SMALL`, per-level `WOLFSSL_NO_ML_KEM_*` exclusions via the `wolfcrypt_mlkem768/1024` pseudomodules (512 always excluded — `cryptography` can't interop with it) |
| `makefiles/suit.base.inc.mk` | `SUIT_MANIFEST_ENCRYPT_ALGO ?= x25519`; algo-aware `SUIT_ENC_KEY` name + `openssl genpkey` (seed-only provparam); `PKG_SOURCE_LOCAL_WOLFSSL` also on `ml-kem-%` |
| `dist/tools/suit/enckey_to_header.py` | Accepts ML-KEM-768/1024 private keys (emits the 64-byte seed) alongside X25519 |
| `examples/advanced/suit_update/Makefile` | Algo-aware module selection + per-algo buffer pad |
| `examples/advanced/suit_update/manifest-encryption-mlkem/` | Standalone interop example (plan Step 1), both levels; `encrypt_manifest.py --key/-o` doubles as the host-side encryption tool |
| `examples/advanced/suit_update/mlkem-feasibility-matrix-raw.txt` | Raw `size`/ld output of the 12-combo samr21 matrix below |

## samr21-xpro feasibility matrix — SUPERSEDED, see the correction above

**⚠️ The table below is link-time-only and known wrong for every ML-KEM
column** (measured before the `WOLFSSL_NO_MALLOC`/stack-sizing fix — see
the correction section above). Kept for history; do not use it to decide
what to build. The corrected, re-measured numbers are in
`FIRMWARE_ENCRYPTION_CHANGES.md`.

`BOARD=samr21-xpro make clean all` per combo; RAM = data+bss of
`suit_update.elf` against 32,768 B; ❌ = `slot0.elf` `.bss` overflow:

| Signing \ Encryption | X25519 | ML-KEM-768 (⚠️ wrong, see above) | ML-KEM-1024 (⚠️ wrong, see above) |
|---|---|---|---|
| Ed25519 | ✅ 102,492 t / 21,160 RAM (11.6 KB spare) | ~~✅ 6.5 KB spare~~ | ~~✅ 5.0 KB spare~~ |
| ML-DSA-44 | ✅ 116,100 t / 30,888 RAM (1.9 KB spare) | ~~✅ 1,160 B spare~~ **❌ actually overflows ~3.4 KB** | ~~✅ 296 B spare~~ |
| ML-DSA-65 | ✅ 116,708 t / 32,680 RAM (88 B spare) | ❌ (still ❌ after correction) | ❌ |
| ML-DSA-87 | ❌ overflow 3,756 B | ❌ | ❌ |

Flash never binds (≤120.3 KB text) — that part of the analysis still
holds; it's purely a RAM/stack story.

## RAM rework: fitting ML-DSA-44 + ML-KEM-768 (recovered 4,388 B) — superseded

**This section's conclusion no longer holds** (see the correction above):
the union and exact-buffer levers below were real and are still in the
code, but they recovered RAM from the wrong side of the ledger — static
`.bss`, when the actual shortfall (once the heap bug is fixed and the
worker stack is correctly sized) is ~3.3–3.6 KB of *stack*, not covered
by either lever. Left here for the historical record of what these two
levers do; they remain in place (harmless, still saves real `.bss`) but
are no longer sufficient on their own to fit this combo.

Two levers (a third — `CONFIG_GNRC_PKTBUF_SIZE=4096`, +2 KB — is wired as
a commented-out reserve in the app Makefile):

1. **`suit_pq_scratch` union** (`sys/include/suit/pq_scratch.h`, definition
   in `sys/suit/encrypt/decrypt.c`, consumed by pkg/libcose patch
   `0003-cose-crypto-share-ML-DSA-verify-state-with-RIOT-s-PQ.patch`): the
   static ML-DSA verify state and the static `MlKemKey` are **never live
   concurrently** (decryption completes, key freed, before `suit_parse()`
   starts signature verification — one worker thread), so they share one
   allocation. Active only when both wolfcrypt modules are compiled
   (`SUIT_PQ_SCRATCH_SHARED`); every single-scheme build keeps its own
   private static and is **byte-identical** to before (verified: mldsa44/65
   + x25519, ed25519 + ml-kem-768, mldsa44 with encryption off).
   Measured: union = 9,344 B = `sizeof(MlDsaKey)`; the ~4 KB `MlKemKey`
   overlays for free.
2. **Exact manifest buffer** for the combo: `SUIT_MANIFEST_BUFSIZE=3904`
   (the encrypted ML-DSA-44 manifest measures exactly 3,776 B + 128 B
   margin) instead of the additive 3072+1216 → +384 B.

Functional verification on native64: **two consecutive** ML-DSA-44-signed,
ML-KEM-768-encrypted updates in one process — each cycle exercises both
union members back-to-back (decrypt writes the scratch, then verify
overwrites it) — both ended in `suit_worker: update successful` with the
payload installed; no state leakage between the overlaid users.

## Verification (native64, 2026-07-19)

| Case | Result |
|---|---|
| Ed25519-signed, ML-KEM-768-encrypted manifest via `suit fetch file:///nvm0/...` | 1416 B container → `manifest decrypted (272 bytes)` → verified → payload installed |
| Tampered container byte | rejected, AEAD error `-213` |
| X25519 container on ML-KEM firmware | `suit: recipient alg -25 != built-in -70768`, rejected in parse |
| Plaintext manifest | passes through, update succeeds |
| Standalone example (both levels) | seed-expansion pubkey match, decrypt MATCH, tampered AEAD ct **and** tampered KEM ct rejected (the latter via FIPS 203 implicit rejection → wrong CEK → tag error) |

## Gotchas / lessons learned

- **Implicit rejection**: a tampered KEM ciphertext never produces a
  decapsulation error — the failure surfaces later as an AEAD tag error.
  Don't add (or test for) an error path at the decapsulation step.
- **Seed vs. expanded key**: embed the 64 B seed, not the 2.4/3.2 KB
  expanded decapsulation key; `MakeKeyWithRandom` re-expands
  deterministically and the standalone example's pubkey comparison guards
  the interop.
- **Stale shared headers** (standalone example): both levels write the same
  `device_*.h`/`encrypted.h` filenames — the script rewrites them on every
  run after this bit us (a 1024 pubkey against a 768 build looks like a
  seed-expansion mismatch / `BUFFER_E` -132).
- **No RNG needed on-device** (decapsulation and seed expansion are
  deterministic), and no symbol collisions this time — `wc_mlkem*` shares
  nothing with the c25519 pkg.
- Security caveats as before: seed embedded in the image
  (prototype-grade), KEM gives no sender authentication (the COSE_Sign1
  signature remains the authenticity anchor), one encrypted artifact per
  device key.
