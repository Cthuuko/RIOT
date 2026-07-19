# SUIT manifest encryption — code changes summary

Companion to `MANIFEST_ENCRYPTION_PLAN.md` (plan + status checklist) in the
style of `MLDSA_MULTILEVEL_CHANGES.md` / `MLDSA_HARDWARE_FIXES.md`: what was
changed to add manifest **confidentiality** to the SUIT workflow, and what
was learned doing it. The combined diff of every file listed below is kept
as `manifest-encryption-implementation.patch` in this directory (same
convention as `mldsa-samr21-hardware-fixes.patch`). Status: verified end-to-end on `native64`
(Ed25519 + encryption: full update; ML-DSA-44 + encryption: decrypt +
signature verify pass). Real-hardware (`samr21-xpro`) bring-up is pending
(plan Step 5b).

## What it does

The host encrypts the *signed* manifest for one specific device
(sign-then-encrypt); the device decrypts it in place before `suit_parse()`
runs (decrypt-then-verify). Authenticity still comes exclusively from the
COSE_Sign1 signature — encryption adds confidentiality, not sender
authentication.

- **Key exchange**: ephemeral-static X25519 ECDH (COSE "ECDH-ES +
  HKDF-256", alg -25). The device owns a static X25519 keypair; the host
  generates a fresh ephemeral key per manifest and ships its public half in
  the container. No online interaction — fits the constrained pull model.
- **KDF**: HKDF-SHA256 over the shared secret, `info` = COSE_KDF_Context
  (RFC 9053 §5.2).
- **AEAD**: ChaCha20-Poly1305 (COSE alg 24), AAD = COSE Enc_structure.
- **Container**: RFC 9770-style COSE_Encrypt (CBOR tag 96) with a single
  recipient carrying the ephemeral public key. Full CBOR layout and the
  byte-exactness rules: `manifest-encryption/README.md`.
- **Overhead**: ~92 B per manifest (32 B ephemeral key + 12 B nonce +
  16 B tag + CBOR framing).

## Opt-out contract

Encryption is **on by default**; `SUIT_MANIFEST_ENCRYPT=0` opts out.

| Side | `SUIT_MANIFEST_ENCRYPT=1` (default) | `=0` |
|---|---|---|
| App build | `USEMODULE += suit_manifest_encrypt`, buffer +128 B, X25519 device key generated + embedded | module, crypto code, and key completely absent — byte-identical behavior to pre-feature builds |
| Device runtime | CBOR tag 96 → decrypt in place, then parse; anything else passes through unchanged (legacy plaintext manifests keep working) | plaintext manifests only |
| Host publish | encrypt after `suit-tool sign` via `manifest-encryption/encrypt_manifest.py --key <device key>` (manual for now) | unchanged pipeline |

## Changed / added files

| File | Change |
|---|---|
| `sys/suit/encrypt/decrypt.c` (new) | The decryption engine: nanocbor COSE_Encrypt parsing, c25519 X25519 ECDH, wolfCrypt HKDF-SHA256 + ChaCha20-Poly1305, in-place decrypt, all-zero shared-secret rejection |
| `sys/suit/encrypt/Makefile` (new) | `MODULE = suit_manifest_encrypt` |
| `sys/include/suit/manifest_encrypt.h` (new) | `suit_manifest_decrypt()` API: 0 = decrypted, `SUIT_MANIFEST_ENCRYPT_PASSTHROUGH` (1) = not encrypted, <0 = error |
| `sys/suit/transport/worker.c` | Hook at the top of `suit_handle_manifest_buf()`: decrypt/pass-through before `suit_parse()`; logs `manifest decrypted (N bytes)` / `manifest not encrypted, passing through` |
| `sys/suit/Makefile` | `DIRS += encrypt` when the module is selected |
| `sys/suit/Makefile.dep` | Module deps: `USEPKG c25519 + wolfssl`, `wolfcrypt{,_chacha,_poly1305,_hmac}` (chacha20_poly1305 auto-selected) |
| `pkg/wolfssl/include/user_settings.h` | `HAVE_HKDF` now defined whenever `wolfcrypt_hmac` is used (HKDF code lives in the always-compiled `kdf.c`) |
| `makefiles/suit.base.inc.mk` | `SUIT_MANIFEST_ENCRYPT ?= 1`; device key handling when the module is selected: `SUIT_ENC_SEC` (default `$(SUIT_KEY_DIR)/device_x25519.pem`, auto-generated via `openssl genpkey -algorithm X25519`, target `suit/genenckey`) → `suit_enc_seckey.h` in `$(BINDIR)/riotbuild/` via `enckey_to_header.py` |
| `dist/tools/suit/enckey_to_header.py` (new) | X25519 private PEM → `const uint8_t suit_enc_seckey[32]` header (RFC 7748 little-endian raw bytes) |
| `examples/advanced/suit_update/Makefile` | Module selection + buffer sizing: `SUIT_MANIFEST_BUFSIZE` +128 B when encryption is on (ed25519: 768, ml-dsa-44/65/87: 3200/3968/5504); opt-out leaves everything untouched |
| `examples/advanced/suit_update/manifest-encryption/` (new, plan Step 1) | Standalone host-only interop example (Python `cryptography` encrypt ↔ wolfCrypt decrypt) + wire-format README; `encrypt_manifest.py` doubles as the host-side encryption tool (`--key`, `-o`) |

## Design decision: c25519 for X25519, not wolfCrypt

The plan originally called for wolfCrypt's curve25519 on-device. That
fails at link time in the default (Ed25519) configuration: wolfCrypt's
`CURVE25519_SMALL` implementation pulls `fe_low_mem.c`, which defines
`fprime_mul`/`fprime_sub` — the same symbols as the c25519 pkg's
`fprime.c`, which is already linked as libcose's Ed25519 verify backend
(both vendor the same dlbeer/c25519 math). Using the c25519 pkg's own
`c25519_smult()` for the ECDH instead:

- resolves the collision without touching either library,
- adds **zero** new curve code to Ed25519 builds (the pkg is already there),
- needs no RNG (no blinding), and no byte-order conversion — c25519 and
  Python's `cryptography` both use RFC 7748 little-endian raw keys.

`c25519_prepare()` applies the RFC 7748 scalar clamping that
`X25519PrivateKey.exchange()` performs implicitly, and the all-zero
shared-secret check mirrors `cryptography`'s small-order rejection.

wolfCrypt still provides HKDF-SHA256 and ChaCha20-Poly1305 (and, in ML-DSA
builds, signature verification — c25519 and wolfCrypt coexist fine as long
as wolfCrypt's curve25519/ed25519 objects are never pulled in).

## Verification (native64, 2026-07-19)

| Case | Result |
|---|---|
| Ed25519-signed, encrypted manifest via `suit fetch file:///nvm0/...` | decrypt → signature verify → payload installed; `storage_content` shows payload |
| Tampered ciphertext byte | rejected before parsing, AEAD auth error `-213` |
| Plaintext manifest on encryption-capable firmware | `manifest not encrypted, passing through` → normal verify/update |
| `SUIT_MANIFEST_ENCRYPT=0` build | compiles, module absent from image (~17 KB smaller text on native64) |
| ML-DSA-44-signed, encrypted manifest | links cleanly, decrypts, signature verify passes (a post-verify condition failure appears identically with the plain manifest on that binary — unrelated to encryption) |

## Gotchas / lessons learned

- **Symbol collision** (above): never enable `wolfcrypt_curve25519` (or
  `wolfcrypt_ed25519`) together with the c25519 pkg.
- **In-place decrypt is safe**: wolfCrypt's `wc_ChaCha20Poly1305_Decrypt`
  explicitly supports `out == in`, and the Poly1305 tag is computed over
  the ciphertext as it is consumed. Plaintext lands *inside* `_manifest_buf`
  at the ciphertext's offset — `suit_parse()` is handed that interior
  pointer, no memmove.
- **Byte-exact CBOR**: the HKDF `info` and the AAD must serialize
  identically on both sides; the device re-uses the *received*
  protected-header bytes verbatim instead of re-encoding its own.
- **Standalone sample only**: host builds of wolfSSL default to
  `WOLFSSL_CURVE25519_BLINDING` → `wc_curve25519_set_rng()` is mandatory or
  the shared secret fails with `BAD_FUNC_ARG` (-173). RIOT device builds
  never hit this (c25519 path).
- **Native testing**: `file://` fetches go through the hostfs mount —
  `file:///nvm0/<file>` maps to `native/<file>` relative to the elf's CWD;
  the worker's URL buffer is 64 chars (`CONFIG_SOCK_URLPATH_MAXLEN`), so
  long host paths get silently truncated.
- **Security caveats (prototype)**: the device private key is embedded in
  the firmware image (`suit_enc_seckey.h`) — real deployments need
  protected key storage. ECDH-ES provides no sender authentication; the
  manifest signature remains the sole authenticity anchor. Per-device
  encryption means one `.enc` artifact per device key.
