# ML-KEM manifest encryption — implementation plan

Living plan document (sibling of `MANIFEST_ENCRYPTION_PLAN.md`, which this
builds on) — future sessions resume from the first unchecked step.

## Context

The SUIT manifest-encryption feature (X25519 ECDH-ES + HKDF-256 + ChaCha20-Poly1305, `MANIFEST_ENCRYPTION_PLAN.md`, verified on native64) uses a classical key exchange. To make manifest confidentiality post-quantum — matching the ML-DSA post-quantum signature work — add **ML-KEM** (FIPS 203) as a selectable alternative to X25519, host side via `cryptography` 48 (https://cryptography.io/en/latest/hazmat/primitives/asymmetric/mlkem/), device side via wolfCrypt's `wc_MlKemKey_*` API. User choices: **both ML-KEM-768 and ML-KEM-1024** (cryptography has no 512), integrated **alongside** X25519 as a selectable algorithm (X25519 stays default). First deliverable: a standalone host-only interop example, mirroring `examples/advanced/suit_update/manifest-encryption/`.

Verified preconditions (no wolfssl rebuild needed this time):

- `cryptography` 48.0.0: `MLKEM768PrivateKey`/`MLKEM1024PrivateKey`, `encapsulate()` → (ss 32 B, ct 1088/1568 B), `private_bytes_raw()` = **64-byte FIPS 203 seed (d‖z)**, `from_seed_bytes()` round-trips.
- Vendored wolfSSL (`dist/tools/suit/ml-dsa-example/wolfssl`) already has ML-KEM compiled: `WOLFSSL_HAVE_MLKEM` in `options.h`, `wc_mlkem.lo`/`wc_mlkem_poly.lo` built; header `wolfssl/wolfcrypt/wc_mlkem.h` provides `wc_MlKemKey_Init(key, WC_ML_KEM_768|WC_ML_KEM_1024, …)`, `wc_MlKemKey_MakeKeyWithRandom(key, rand, 64)` (deterministic from d‖z — the seed interop path), `wc_MlKemKey_Decapsulate`, `wc_MlKemKey_DecodePrivateKey`.
- RIOT's `pkg/wolfssl` has **no** mlkem module mapping yet (device integration will special-case `wc_mlkem.c`+`wc_mlkem_poly.c` like `wc_mldsa.c`).

## Status checklist

- [x] Step 0: persist this plan as `examples/advanced/suit_update/MLKEM_ENCRYPTION_PLAN.md` (2026-07-19)
- [x] Step 1: standalone interop example `examples/advanced/suit_update/manifest-encryption-mlkem/` (2026-07-19), both levels verified: seed-expansion interop check OK (wolfCrypt `MakeKeyWithRandom` ≡ cryptography `from_seed_bytes`), Python-encrypted → wolfCrypt-decrypted MATCH, tampered AEAD ct rejected (-213), tampered KEM ct rejected via FIPS 203 implicit rejection (wrong ss → wrong CEK → tag error, **no** decapsulation error code), 3.7 KB payloads OK. Measured container overheads: **1144 B (768) / 1624 B (1024)**. Gotcha found: the per-level Python runs share header filenames — the script now always rewrites `device_*.h`/`encrypted.h` so a stale other-level header can't poison the C build (symptom: pubkey-interop MISMATCH / `DecodePublicKey` BUFFER_E -132).
- [x] Step 2: device integration (2026-07-19) — **compile-time** algorithm dispatch in `sys/suit/encrypt/decrypt.c` (`#ifdef MODULE_WOLFCRYPT_MLKEM` selects the KEM recipient path; the received recipient alg ID is validated against the built-in one, so a container for the wrong scheme is rejected with a clear log line). New `wolfcrypt_mlkem` pseudomodule: `wc_mlkem.c`+`wc_mlkem_poly.c` special-cased in `pkg/wolfssl/Makefile.wolfcrypt`, SHA3 dep in `pkg/wolfssl/Makefile.dep`, `user_settings.h` gates (`WOLFSSL_HAVE_MLKEM`, SHAKE, `WOLFSSL_MLKEM_SMALL`, per-level `WOLFSSL_NO_ML_KEM_*` exclusions via `wolfcrypt_mlkem768/1024`). `PKG_SOURCE_LOCAL_WOLFSSL` now also triggers on `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-%` (`makefiles/suit.base.inc.mk`).
- [x] Step 3: host tooling (2026-07-19) — `SUIT_MANIFEST_ENCRYPT_ALGO`-aware device-key gen in `suit.base.inc.mk` (`openssl genpkey -algorithm ml-kem-*` with seed-only provparam; key name `device_mlkem768/1024`), `enckey_to_header.py` accepts X25519 + ML-KEM (emits the 64-byte seed); manifest encryption via `manifest-encryption-mlkem/encrypt_manifest.py --key ... -o ...` after `suit-tool sign` (manual, like the X25519 flow)
- [x] Step 4: buffer sizing (2026-07-19) — app Makefile adds +1216 B (768) / +1696 B (1024) instead of +128 B when a KEM algo is selected
- [x] Step 5a: E2E native64 (2026-07-19, Ed25519 + ML-KEM-768): encrypted manifest (1416 B) → `manifest decrypted (272 bytes)` → signature verified → payload installed; tampered container rejected (-213); an X25519 container on ML-KEM firmware rejected with `recipient alg -25 != built-in -70768`; plaintext manifest passes through.
- [x] Step 5b (link-time part, 2026-07-19): samr21-xpro **feasibility matrix measured for all 12 combos** — see the table below. Fits: Ed25519 × {X25519, ML-KEM-768, ML-KEM-1024}, ML-DSA-44 × X25519, ML-DSA-65 × X25519 (88 B spare). Everything else overflows RAM. Remaining: hardware E2E on the fitting combos (start Ed25519 + ML-KEM-768), and optionally the workspace-union mitigation to chase ML-DSA-44 + ML-KEM-768 (misses by 3,228 B).
- [ ] Step 6: docs (MLKEM_ENCRYPTION_CHANGES.md, NATIVE_SETUP/SAMR21 updates, CLAUDE.md) + refresh implementation patch

## Design

Same sign-then-encrypt / decrypt-then-verify composition and COSE_Encrypt container as X25519; only the recipient changes — a KEM has no ephemeral public key, so the **encapsulation ciphertext rides in the recipient's ciphertext field** (the natural COSE slot, empty in the X25519 variant):

```
96([
  << {1: 24} >>,            / protected: alg = ChaCha20/Poly1305        /
  {5: h'<nonce 12B>'},      / unprotected                               /
  h'<ciphertext || tag>',
  [[
    << {1: <KEM alg> } >>,  / protected: -70768 (ML-KEM-768) or         /
                            /            -70769 (ML-KEM-1024) —        /
                            / private-use IDs, no IANA COSE alg exists  /
    {},                     / unprotected: empty (no ephemeral key)     /
    h'<KEM ct 1088|1568B>'  / ML-KEM encapsulation ciphertext           /
  ]]
])
```

- `CEK = HKDF-SHA256(ikm=ss, salt=empty, len=32, info=COSE_KDF_Context)` and `AAD = Enc_structure` — **identical code/rules** as X25519 (byte-exact CBOR, device reuses received protected-header bytes verbatim). The KDF context's AlgorithmID stays 24; the recipient-protected bstr inside SuppPubInfo naturally binds the KEM alg ID.
- Device key: the **64-byte seed** is what gets stored/embedded (`from_seed_bytes` ↔ `wc_MlKemKey_MakeKeyWithRandom`) — header stays tiny even though the expanded dk is 2.4 KB.
- Interop risk to verify in Step 1: wolfCrypt's `MakeKeyWithRandom` must expand the same d‖z seed to the same keypair as `cryptography` (confirm by comparing the 1184/1568-byte encapsulation keys), and implicit-rejection behavior on tampered KEM ct must still fail the AEAD (ss differs → wrong CEK → tag failure, not a KEM error).

## Step 1 files — `examples/advanced/suit_update/manifest-encryption-mlkem/`

One parameterized folder instead of two near-copies (deviation from the ml-dsa-44/87 twin-folder pattern, to avoid duplication):

1. **`encrypt_manifest.py`** — clone the structure of `../manifest-encryption/encrypt_manifest.py` (same `format_byte_array`, `kdf_context`, `enc_structure`, self-test shape), with `--level {768,1024}` (default 768) selecting `MLKEM768PrivateKey`/`MLKEM1024PrivateKey` and the alg ID. Keygen writes `device_mlkem<level>.pem` (PKCS8 via `private_bytes`), `device_seckey.h` (64-byte seed) + `device_pubkey.h`; encrypt writes `manifest.cose`, `encrypted.h`, `plaintext.h`; self-test decapsulates + decrypts in Python.
2. **`wolfcrypt-sample.c`** — clone of `../manifest-encryption/wolfcrypt-sample.c` with the ECDH block replaced by: `wc_MlKemKey_Init(&key, MLKEM_TYPE, …)` → `wc_MlKemKey_MakeKeyWithRandom(seed 64B)` → (interop check: `wc_MlKemKey_EncodePublicKey` == Python's pubkey, embedded via `device_pubkey.h`) → `wc_MlKemKey_Decapsulate(ct)` → HKDF → ChaCha20-Poly1305. `-DMLKEM_LEVEL=768|1024` picks the parameter set (default 768). Negative tests: tampered AEAD ciphertext AND tampered KEM ciphertext (both must fail the tag). nanocbor parse identical to the X25519 sample except the recipient (get bstr instead of entering a COSE_Key map).
3. **`README.md`** — wire format, both build/run command sets (768 and 1024), seed-interop explanation, quick-reference table (ct/pk/seed sizes per level), no-rebuild note, security caveats; same skeleton as `../manifest-encryption/README.md`.

Build command (no wolfssl reconfigure — ML-KEM already in the built lib):

```bash
python3 encrypt_manifest.py --level 768
gcc wolfcrypt-sample.c ../../../../build/pkg/nanocbor/src/{decoder,encoder}.c \
    -DMLKEM_LEVEL=768 -o mlkem768-example \
    -I../../../../build/pkg/nanocbor/include \
    -I../../../../dist/tools/suit/ml-dsa-example/wolfssl \
    -L../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs \
    -Wl,-rpath,"$(realpath ../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs)" -lwolfssl
./mlkem768-example      # repeat with --level 1024 / -DMLKEM_LEVEL=1024
```

## samr21-xpro feasibility matrix (signing × encryption)

The 32 KB RAM budget is the binding constraint; every number below builds on the measured baselines (`MLDSA_HARDWARE_FIXES.md`: ML-DSA-65 build = 32,552/32,768 B, **~216 B spare**; ML-DSA-44 links "with headroom to spare" — unquantified; ML-DSA-87 overflows `.bss` by **3,628 B** even without encryption; Ed25519 has ample room).

Per-combo added RAM when swapping X25519 → ML-KEM (all static/.bss unless noted):

- **Manifest buffer**: +1,216 B (ML-KEM-768 ct 1088 + framing) or +1,696 B (1024) *instead of* the +128 B X25519 overhead → net +1,088/+1,568 B vs the current encrypted sizing.
- **`MlKemKey` state**: multi-KB (k=3: ≈3–4 KB, k=4: ≈4–5 KB with `WOLFSSL_MLKEM_SMALL`); per the ML-DSA lesson it must be `static`, so it lands in `.bss`. Decapsulation stack on top (SMALL variant, estimated 1–2 KB — must fit or grow the 4 KB worker stack).
- The 64-byte seed key replaces the 32-byte X25519 key in flash (negligible).

**MEASURED link results (2026-07-19, `BOARD=samr21-xpro make clean all` per combo; RAM = data+bss of `suit_update.elf` against 32,768 B; ❌ = `.bss` overflow of `slot0.elf` by the stated bytes):**

| Signing \ Encryption | X25519 | ML-KEM-768 | ML-KEM-1024 |
|---|---|---|---|
| Ed25519 | ✅ 102,492 t / 21,160 RAM (**11.6 KB spare**) | ✅ 114,144 t / 26,264 RAM (**6.5 KB spare**) | ✅ 113,952 t / 27,768 RAM (**5.0 KB spare**) |
| ML-DSA-44 | ✅ 116,100 t / 30,888 RAM (**1.9 KB spare**) | ✅ **31,608 RAM (1,160 B spare)** after the pq_scratch rework (was ❌ 3,228 B over) | ✅ 32,472 RAM (296 B spare, experimental) after the rework (was ❌ 4,732 B over) |
| ML-DSA-65 | ✅ 116,708 t / 32,680 RAM (**88 B spare** — exactly the predicted ≲90 B) | ❌ still ~0.6 KB+ over even post-rework | ❌ overflow 6,524 B |
| ML-DSA-87 | ❌ overflow 3,756 B | ❌ overflow 8,860 B | ❌ overflow 10,364 B |

Conclusions:

- **Ed25519 + ML-KEM-768/1024 both fit comfortably**; after the RAM rework (see `MLKEM_ENCRYPTION_CHANGES.md` "RAM rework": `suit_pq_scratch` union overlaying the never-concurrent ML-DSA verify state and `MlKemKey` [recovers ~4 KB], + exact 3,904 B manifest buffer [+384 B]) the **full-PQ combo ML-DSA-44 + ML-KEM-768 fits with 1,160 B spare** — verified by double-update E2E on native64. The reserve lever (`CONFIG_GNRC_PKTBUF_SIZE=4096`, +2 KB) stays commented out in the app Makefile.
- ML-DSA-65/87 + any KEM remain dead ends on 32 KB RAM (documented like Example D).
- Flash is never the binding constraint (max 120.3 KB text).

Raw ld/size output (original 12 + post-rework re-measurements): `mlkem-feasibility-matrix-raw.txt`, summarized in `MLKEM_ENCRYPTION_CHANGES.md`.

## Later steps (outline — not this round)

- **Step 2 device integration**: dispatch in `sys/suit/encrypt/decrypt.c` on the recipient protected alg (−25 → existing c25519 path; −70768/−70769 → `wc_MlKemKey_MakeKeyWithRandom` + `Decapsulate`), compiled per `SUIT_MANIFEST_ENCRYPT_ALGO`. New `wolfcrypt_mlkem` pseudomodule: special-case `wc_mlkem.c` + `wc_mlkem_poly.c` in `pkg/wolfssl/Makefile.wolfcrypt` (the `wc_mldsa.c` pattern) and gate `WOLFSSL_HAVE_MLKEM`/`WOLFSSL_WC_ML_KEM_768`(/1024)/`WOLFSSL_MLKEM_SMALL`-family defines in `user_settings.h`; needs `PKG_SOURCE_LOCAL_WOLFSSL` (pinned wolfssl predates ML-KEM) — extend the `suit.base.inc.mk` ml-dsa condition to also trigger on `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-%`. No symbol-collision concern this time (mlkem shares nothing with c25519), and decapsulation needs no RNG.
- **Step 3 host tooling**: `SUIT_ENC_KEY_ALGO`-aware keygen in `suit.base.inc.mk` (`openssl genpkey -algorithm ml-kem-768 -provparam ml-kem.output_formats=seed-only` — same OpenSSL 3.5+/seed-only lesson as ML-DSA), `enckey_to_header.py` extended to emit the 64-byte seed + an algo define.
- **Step 4**: buffer +1216 B (768) / +1696 B (1024) instead of +128 when a KEM algo is selected.
- **Step 5 samr21 feasibility**: `MlKemKey` is multi-KB — apply the ML-DSA lesson (static allocation, never on the 4 KB worker stack); measure before promising anything beyond Ed25519+ML-KEM-768.
- **Step 6**: docs + regenerate `manifest-encryption-implementation.patch` (or a separate mlkem patch).

## Verification (Step 1)

1. `python3 encrypt_manifest.py --level 768` → keygen + `Self-test decrypt: OK`; same for `--level 1024`.
2. Build + run both C samples: public-key interop check passes (seed expansion matches), `Python-encrypted, wolfCrypt-decrypted: MATCH`, tampered AEAD ciphertext rejected (-213), tampered KEM ciphertext rejected (tag failure via implicit rejection).
3. Encrypt a 3.7 KB manifest-sized file with each level and re-run (bstr length-field growth).
4. No wolfssl rebuild involved → no ml-dsa regression run needed.
