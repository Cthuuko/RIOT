# Encrypted SUIT manifest via X25519 key exchange — implementation plan

Living plan document — future sessions resume from the first unchecked step.

## Status checklist

- [x] Step 0: this plan saved to the repo
- [x] Prerequisite: vendored wolfSSL rebuilt with `--enable-curve25519` (2026-07-18; `HAVE_CURVE25519` now in its `options.h`)
- [x] Step 1: standalone E2E example in `manifest-encryption/` (encrypt_manifest.py, wolfcrypt-sample.c, README.md) verified end-to-end (2026-07-18: interop MATCH, tamper rejected, 3.7KB manifest-sized payload OK, ml-dsa-44 regression OK; gotcha found: `WOLFSSL_CURVE25519_BLINDING` is on by default → `wc_curve25519_set_rng()` required or shared-secret fails with -173)
- [x] Step 2: device integration (2026-07-19, verified E2E on native64 with Ed25519: encrypted fetch decrypts+verifies+installs, tampered container rejected with -213, plaintext manifest passes through, `SUIT_MANIFEST_ENCRYPT=0` opt-out build excludes the module entirely). **Design deviation from the original plan**: on-device X25519 uses the **c25519 pkg** (already linked as libcose's Ed25519 verify backend), NOT wolfCrypt's curve25519 — wolfCrypt's `CURVE25519_SMALL` pulls `fe_low_mem.c`, whose `fprime_*` symbols collide at link time with the c25519 pkg's `fprime.c` (both vendor the same dlbeer math). wolfCrypt still does HKDF-SHA256 (+ new `HAVE_HKDF` gate on `wolfcrypt_hmac` in `pkg/wolfssl/include/user_settings.h`) and ChaCha20-Poly1305. Bonus: c25519 needs no RNG (no blinding) and adds zero new curve code to the samr21 image. New files: `sys/suit/encrypt/decrypt.c`, `sys/include/suit/manifest_encrypt.h`, `dist/tools/suit/enckey_to_header.py`; hooks in `sys/suit/transport/worker.c` (`suit_handle_manifest_buf`), `sys/suit/Makefile{,.dep}`, `makefiles/suit.base.inc.mk` (X25519 device-key gen + `suit_enc_seckey.h` header + `suit/genenckey` target), app `Makefile` (module + buffer sizing).
- [x] Step 3: host tooling — key gen/header embedding landed with Step 2 (`SUIT_ENC_SEC`, `suit/genenckey`); manifest encryption itself stays a manual `encrypt_manifest.py --key ... -o ...` call after `suit-tool sign` for now (a `suit/publish`-integrated encrypt step can follow if wanted)
- [x] Step 4: buffer sizing — app Makefile adds +128 B to `SUIT_MANIFEST_BUFSIZE` for all key algos when encryption is on (ed25519: 768, ml-dsa-44: 3200, -65: 3968, -87: 5504)
- [x] Step 5a: E2E on native64 (2026-07-19) — Ed25519+encryption: full update success (decrypt → verify → install, `storage_content` shows payload); ML-DSA-44+encryption: builds and links cleanly (c25519 + wolfcrypt coexist), decrypts and passes signature verification — accepted per test criterion (decrypt path works, pass-through works); a downstream condition failure (`res=-4` plain / `res=-1` encrypted, after successful signature verify) exists identically in the plain path on that binary, i.e. unrelated to encryption. Note: native VFS fetches use `file:///nvm0/<file>` (host dir `native/` next to the elf's CWD); the worker URL buffer is 64 chars.
- [ ] Step 5b: E2E on samr21-xpro (see feasibility section; recommended pairing Ed25519+encryption first)
- [x] Step 6 (partial, 2026-07-19): `MANIFEST_ENCRYPTION_CHANGES.md` written (changes, opt-out contract, verification, gotchas); `NATIVE_SETUP.md` extended with the encrypted flow (step 6b encrypt-after-sign, step 9 opt-out, pass-through/tamper log lines, `/nvm0` vfs tip); `CLAUDE.md` updated (file tables, E2E reference, gotchas bullet). `SAMR21_EXAMPLES.md` now carries Example E, a draft encrypted-manifest walkthrough explicitly banner-marked **UNVERIFIED** with an E.9 bring-up checklist — Step 5b executes that checklist, then drops the banner and records the measured sizes.

## Context

The SUIT workflow today provides only authenticity/integrity (COSE_Sign1 with Ed25519 or ML-DSA); manifests travel in plaintext. Goal: add confidentiality by encrypting the signed manifest on the host and decrypting it on-device before `suit_parse()` runs. Chosen design (confirmed with user):

- **Key exchange**: ephemeral–static X25519 ECDH. The device owns a static X25519 keypair; the host generates a fresh ephemeral keypair per manifest and ships the ephemeral public key inside the container. No online interaction needed — fits the constrained-device pull model.
- **KDF**: HKDF-SHA256 over the ECDH shared secret (COSE "ECDH-ES + HKDF-256", alg **-25**).
- **AEAD**: **ChaCha20-Poly1305** (COSE alg **24**) — constant-time and fast in software on the Cortex-M0+ samr21.
- **Wire format**: **RFC 9770-style COSE_Encrypt** (not Encrypt0 — ECDH-ES needs a recipient structure to carry the ephemeral public key):
  `96([protected{1:24}, unprotected{5:nonce12}, ciphertext, [[protected{1:-25}, unprotected{-1:COSE_Key(OKP,X25519,ephemeral_pub)}, h'']]])`
  - CEK derived per RFC 9053 §5.2: `HKDF-SHA256(ikm=shared_secret, salt=empty, info=COSE_KDF_Context(alg=24, ...), len=32)`
  - AAD = COSE `Enc_structure` `["Encrypt", protected_hdr_bstr, external_aad=h'']`
- **Composition** (for later integration): sign-then-encrypt on host → decrypt-then-verify on device. Step 1 encrypts an arbitrary file to prove interop.

**Step 1 scope (this plan)**: a standalone host-only E2E example in `examples/advanced/suit_update/manifest-encryption/`, mirroring the proven `dist/tools/suit/ml-dsa-example/ml-dsa-44/` pattern: a Python script (cryptography.io, per https://cryptography.io/en/48.0.0/hazmat/primitives/asymmetric/x25519/) encrypts; a plain-gcc `wolfcrypt-sample.c` decrypts and verifies interop. RIOT/device integration is a later step (outlined at the end, not implemented now).

## Step 0: persist this plan in the repo

Copy this plan to `examples/advanced/suit_update/MANIFEST_ENCRYPTION_PLAN.md` (with a status checklist per step) so future sessions can resume from any step; keep the checklist updated as steps complete.

## Prerequisite: enable Curve25519 in the vendored wolfSSL

The vendored checkout at `dist/tools/suit/ml-dsa-example/wolfssl/` (used by the ML-DSA work via `PKG_SOURCE_LOCAL_WOLFSSL`, see `makefiles/suit.base.inc.mk:38-40`) has `HAVE_HKDF`, `HAVE_CHACHA`, `HAVE_AESGCM` in `wolfssl/options.h` but **no `HAVE_CURVE25519`**. One-time rebuild (no sudo; I can run it):

```bash
cd dist/tools/suit/ml-dsa-example/wolfssl
./configure --enable-dilithium --enable-experimental --enable-curve25519 \
  CPPFLAGS=-DWOLFSSL_MLDSA_NO_CTX
make -j4
```

This keeps the existing ML-DSA config intact (same flags + one addition), so the ml-dsa standalone samples keep working.

## New files — `examples/advanced/suit_update/manifest-encryption/`

### 1. `encrypt_manifest.py` (host side, Python `cryptography` + `cbor2`)

Follows the structure of `dist/tools/suit/ml-dsa-example/ml-dsa-44/test_ml_dsa_key.py` (incl. its `format_byte_array()` C-header emitter). Subcommands/phases:

1. **Device keygen**: `X25519PrivateKey.generate()` → write
   - `device_x25519.pem` (host-side copy, PKCS8),
   - `device_seckey.h` (`const byte device_seckey[32]` — raw private scalar, for the C sample / later firmware embedding),
   - `device_pubkey.h` (`const byte device_pubkey[32]`).
2. **Encrypt**: read input file (test payload: a fixed message, or optionally a real signed manifest passed as argv) →
   - ephemeral `X25519PrivateKey.generate()`, `exchange(device_pub)` → shared secret,
   - `HKDF(SHA256, 32, salt=None, info=COSE_KDF_Context)` → CEK,
   - `ChaCha20Poly1305(CEK).encrypt(nonce, plaintext, aad=Enc_structure)` with random 12-byte nonce,
   - assemble COSE_Encrypt with `cbor2`, write `manifest.cose`.
3. **Self-test**: decrypt `manifest.cose` back with the device private key in Python and compare — proves the format round-trips before touching C.

### 2. `wolfcrypt-sample.c` (device-side logic, standalone gcc build)

Mirrors `ml-dsa-44/wolfcrypt-sample.c`. Steps:

1. Read `manifest.cose` (or `#include` a generated `ciphertext.h` — follow the ml-dsa pattern of header embedding to keep the binary self-contained).
2. Parse COSE_Encrypt with **nanocbor** (compiled from the existing checkout `build/pkg/nanocbor/src/decoder.c` + its `include/`): extract protected header, nonce, ciphertext, ephemeral public key.
3. `wc_curve25519_import_private_raw` / `wc_curve25519_import_public_ex` + `wc_curve25519_shared_secret_ex(..., EC25519_LITTLE_ENDIAN)` — endianness flag matters for interop with cryptography.io.
4. `wc_HKDF(WC_SHA256, shared, 32, NULL, 0, info, infoSz, cek, 32)`.
5. Rebuild the `Enc_structure` AAD bytes (re-encode with nanocbor encoder or reuse the exact protected-header bytes from parsing), then `wc_ChaCha20Poly1305_Decrypt(cek, nonce, aad, aadSz, ct, ctSz, tag, plaintext)`.
6. Print recovered plaintext, compare to expected; also run a **negative test** (flip a ciphertext byte → decrypt must fail).

### 3. `README.md`

Modeled on `dist/tools/suit/ml-dsa-example/ml-dsa-44/README.md`: the wolfssl rebuild step, then:

```bash
python3 encrypt_manifest.py            # keygen + encrypt + python self-test
gcc wolfcrypt-sample.c <RIOT>/build/pkg/nanocbor/src/decoder.c \
  -I<RIOT>/build/pkg/nanocbor/include \
  -I../../../../dist/tools/suit/ml-dsa-example/wolfssl \
  -L../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs \
  -Wl,-rpath,... -lwolfssl -o manifest-encryption-example
./manifest-encryption-example          # wolfCrypt decrypt + interop check
```

Plus a short "wire format" section documenting the COSE_Encrypt layout and KDF context, and the security caveat (device private key embedded in a header — prototype only).

## Interop pitfalls to handle explicitly

- **X25519 endianness**: wolfCrypt defaults to big-endian scalars; cryptography.io raw bytes are little-endian (RFC 7748). Use the `_ex` import/shared-secret variants with `EC25519_LITTLE_ENDIAN`.
- **AAD byte-exactness**: the `Enc_structure` AAD must serialize identically on both sides — reuse the received protected-header bstr verbatim on the C side rather than re-encoding it.
- **KDF context**: keep the `info` structure simple and byte-identical on both sides (documented in README); this is the analogue of the ML-DSA `VerifyCtx` lesson recorded in CLAUDE.md.

## Verification

1. `python3 encrypt_manifest.py` — self-test decrypt passes.
2. Build + run `manifest-encryption-example` — prints recovered plaintext matching input; tampered-ciphertext case rejected with a Poly1305 tag error.
3. Re-run the existing `ml-dsa-44` sample once after the wolfssl rebuild to confirm the reconfigure didn't break ML-DSA.

## samr21-xpro feasibility review (stack / flash / RAM)

Step 1 itself is host-only (zero device footprint); this section budgets the **later device integration** on samr21-xpro (Cortex-M0+, 32KB RAM, 256KB flash, riotboot slot ≈ 122KB). Baseline numbers from `MLDSA_HARDWARE_FIXES.md`: the ML-DSA-65 build is 106,012 B text / 260 B data / 32,292 B bss+stack = **32,552 / 32,768 B RAM — only ~216 B spare**.

**Flash**: adding `curve25519.c` (~5–8 KB), `chacha.c`+`poly1305.c`+`chacha20_poly1305.c` (~4 KB), HKDF glue on the already-present SHA-256/HMAC (~1 KB), COSE_Encrypt parsing via the already-linked nanocbor (~1 KB) ≈ **+11–14 KB text** → ~117–120 KB, still inside the ~122 KB slot but close; `CURVE25519_SMALL` is the fallback knob if it overflows. With Ed25519 signing instead of ML-DSA the firmware is tens of KB smaller — no flash concern at all.

**RAM (static)**: the crypto itself adds essentially no .bss (all contexts are small and stack-transient; device private key is a 32 B `const` in flash). The real cost is `SUIT_MANIFEST_BUFSIZE`: the COSE_Encrypt container adds ~130 B (32 ephemeral pub + 12 nonce + 16 tag + ~70 CBOR framing), so 3840 → 3968/4096 = **+128–256 B static RAM against a 216 B budget**. Mandatory mitigations:
- **Decrypt in place** in `_manifest_buf` (ChaCha20 is a stream cipher; output may overlap input) — no second buffer, ever.
- Keep 3968 (not a round 4096) exactly like the existing 3840 sizing trick.
- If still over: ML-DSA-44 instead of -65 (buffer 3072→3200, smaller `MlDsaKey` state frees several KB), or reclaim from GNRC pktbuf/main stack.

**Stack**: decrypt runs on the SUIT worker thread (`SUIT_WORKER_STACKSIZE=4096`) *before* signature verification, so the peak is `max(decrypt, verify)`, not the sum. Estimated decrypt peak: X25519 shared secret ~1 KB + HKDF/HMAC-SHA256 ~350 B + ChaCha20-Poly1305 ~400 B (sequential) ≈ well under the verify path that already fits in 4 KB. Per the Fix-2 lesson (12.4 KB `MlDsaKey` on-stack → silent .bss corruption, M0+ has no MPU), any struct that surprises us goes `static`, and stack headroom gets re-measured with `ps`/stack painting after integration.

**Verdict**: feasible on samr21-xpro, but **ML-DSA-65 + encryption leaves ≲ 90 B RAM slack** — acceptable only if measurement confirms it. Recommended hardware pairing: **Ed25519 + encryption** as the primary samr21 demo (ample headroom), **ML-DSA-44 + encryption** as the post-quantum samr21 stretch goal, ML-DSA-65/87 + encryption demonstrated on native64. Gate: `arm-none-eabi-size` + repeated OTA cycles before declaring any pairing supported, same as the ML-DSA bring-up.

## Encryption as opt-out

Manifest encryption will be **on by default, with an explicit opt-out**, controlled by a single Make variable visible on both sides:

- `SUIT_MANIFEST_ENCRYPT ?= 1` in `makefiles/suit.base.inc.mk` (alongside `SUIT_KEY_ALGO`); building with `SUIT_MANIFEST_ENCRYPT=0` opts out.
- **Host side**: the `suit/publish` / manifest pipeline appends the encrypt step (Python tool from this example, later `dist/tools/suit/encrypt_manifest.py`) only when the flag is 1; with 0 it publishes the plain signed manifest exactly as today.
- **Device side**: the flag selects the `suit_manifest_encrypt` pseudomodule. When compiled in, the decrypt hook **auto-detects the container**: CBOR tag 96 (COSE_Encrypt) → decrypt in place, then parse; a plain COSE_Sign1/SUIT envelope → pass through unchanged. This keeps an encryption-capable firmware able to accept legacy plaintext manifests during migration. Optionally a hardening knob (`SUIT_MANIFEST_ENCRYPT_REQUIRED`) later rejects plaintext manifests outright.
- With `SUIT_MANIFEST_ENCRYPT=0` the module, wolfCrypt curve25519/chacha sources, and the embedded device key header are not compiled at all — zero flash/RAM cost, byte-identical behavior to today (important for the tight samr21 ML-DSA-65 budget above: opting out is the escape hatch on that config).
- The standalone Step-1 example is unaffected (it is always-encrypt by nature), but its README documents the flag so the integration contract is written down from day one.

## Later steps (outline only — not in this change)

1. **Device integration**: decrypt hook in `sys/suit/transport/worker.c:124-133` (`suit_handle_manifest_buf`, before `suit_parse` at line 133), decrypting `_manifest_buf` in place; new pseudomodule (e.g. `suit_manifest_encrypt`) pulling `wolfcrypt_curve25519` + `wolfcrypt_chacha` + `wolfcrypt_poly1305` + HKDF, following the ML-DSA Makefile pattern in `examples/advanced/suit_update/Makefile` (~lines 45-72). Gated by `SUIT_MANIFEST_ENCRYPT` (see opt-out section).
2. **Host tooling**: `suit/encrypt` make target in `Makefile.suit.custom` / `makefiles/suit.base.inc.mk`, gated by the same flag; device-key header generation analogous to `dist/tools/suit/pubkey_to_header.py` (`makefiles/suit.base.inc.mk:104-107`).
3. **Buffer sizing**: bump `SUIT_MANIFEST_BUFSIZE` by the ~130-byte COSE_Encrypt overhead.
4. **E2E on native64 then samr21-xpro** per `NATIVE_SETUP.md` / `SAMR21_EXAMPLES.md`.
5. **Documentation (final step)**, following the pattern set by the ML-DSA docs:
   - Update `examples/advanced/suit_update/NATIVE_SETUP.md` and `SAMR21_EXAMPLES.md`: extend each E2E recipe to show **both** paths — the default encrypted flow (`SUIT_MANIFEST_ENCRYPT=1`: device keygen, encrypt-after-sign publish step, expected device-side decrypt log lines) and the opt-out plaintext flow (`SUIT_MANIFEST_ENCRYPT=0`, identical to today), including mixed-mode behavior (encryption-capable firmware accepting a plaintext manifest) and the samr21 RAM caveats from the feasibility section.
   - New `examples/advanced/suit_update/MANIFEST_ENCRYPTION_CHANGES.md`: summary of all changed/added code (analogous to `MLDSA_MULTILEVEL_CHANGES.md` / `MLDSA_HARDWARE_FIXES.md`) — wire format, key derivation, per-file change list, opt-out contract, measured size/stack numbers, and interop lessons learned.
   - Update `examples/advanced/suit_update/CLAUDE.md`: add the new example subfolder and docs to the file tables, a Gotchas bullet for manifest encryption (flag semantics, endianness/AAD interop pitfalls, wolfssl `--enable-curve25519` rebuild requirement, buffer sizing), and list `MANIFEST_ENCRYPTION_CHANGES.md` among the canonical E2E references.
