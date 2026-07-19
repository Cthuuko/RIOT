# ML-KEM manifest encryption — code changes summary

Companion to `MLKEM_ENCRYPTION_PLAN.md` (plan + status checklist), extending
the X25519 manifest-encryption feature (`MANIFEST_ENCRYPTION_CHANGES.md`)
with a **post-quantum key-establishment alternative**: ML-KEM-768/1024
(FIPS 203), selectable via `SUIT_MANIFEST_ENCRYPT_ALGO` (X25519 stays the
default). Status: verified end-to-end on `native64` (Ed25519 signing +
ML-KEM-768 encryption); samr21-xpro **link-level feasibility measured for
all 12 signing × encryption combos** (table below), hardware E2E pending.
The combined diff of the ML-KEM changes is kept as
`mlkem-encryption-implementation.patch` in this directory (same convention
as `manifest-encryption-implementation.patch` for the X25519 feature).

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

## samr21-xpro feasibility matrix (measured 2026-07-19)

`BOARD=samr21-xpro make clean all` per combo; RAM = data+bss of
`suit_update.elf` against 32,768 B; ❌ = `slot0.elf` `.bss` overflow:

| Signing \ Encryption | X25519 | ML-KEM-768 | ML-KEM-1024 |
|---|---|---|---|
| Ed25519 | ✅ 102,492 t / 21,160 RAM (11.6 KB spare) | ✅ 114,144 t / 26,264 RAM (6.5 KB spare) | ✅ 113,952 t / 27,768 RAM (5.0 KB spare) |
| ML-DSA-44 | ✅ 116,100 t / 30,888 RAM (1.9 KB spare) | ❌ overflow 3,228 B | ❌ overflow 4,732 B |
| ML-DSA-65 | ✅ 116,708 t / 32,680 RAM (88 B spare) | ❌ overflow 5,020 B | ❌ overflow 6,524 B |
| ML-DSA-87 | ❌ overflow 3,756 B | ❌ overflow 8,860 B | ❌ overflow 10,364 B |

Takeaways: **Ed25519 + ML-KEM-768/1024 fit with kilobytes to spare** (the
recommended PQ-confidentiality demos); the full-PQ goal
**ML-DSA-44 + ML-KEM-768 misses by only 3,228 B** — candidate mitigations:
union the never-concurrent ML-DSA verify state and `MlKemKey` into one
static workspace, shrink GNRC pktbuf, exact buffer values. ML-DSA-65
survives only with X25519 (88 B spare — matching
`MLDSA_HARDWARE_FIXES.md`'s ~216 B baseline minus the +128 B buffer pad).
Flash never binds (≤116.7 KB text). Link-level verdicts only — hardware
E2E for the fitting combos is plan Step 5b's remaining half.

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
