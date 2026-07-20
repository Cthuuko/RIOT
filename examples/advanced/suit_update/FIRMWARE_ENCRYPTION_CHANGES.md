# SUIT firmware-payload encryption — code changes summary

Companion to `FIRMWARE_ENCRYPTION_PLAN.md` (plan + status checklist), in
the style of `MANIFEST_ENCRYPTION_CHANGES.md` / `MLKEM_ENCRYPTION_CHANGES.md`:
what was changed to add firmware **image confidentiality** to the SUIT
workflow, and what was learned doing it. The combined diff of every file
listed below (code + docs, same convention as
`manifest-encryption-implementation.patch`) is kept as
`firmware-encryption-implementation.patch` in this directory; measured
link outputs in `fw-encryption-feasibility-raw.txt`. Status: verified end-to-end on
`native64` (X25519 recipient, both CoAP and VFS transports); samr21-xpro
link-level feasibility measured (table below); real-hardware E2E pending
(SAMR21_EXAMPLES.md Example F is an **unverified draft**).

## What it does

The host encrypts the firmware payload for one specific device; the device
decrypts it **while it streams in from the transport** and stores only
plaintext. Unlike the manifest (which fits in a buffer and is decrypted in
place before parsing), a firmware image (~120 KB on real boards) never fits
in RAM — hence streaming, using wolfCrypt's incremental ChaCha20-Poly1305
API (`wc_ChaCha20Poly1305_Init/UpdateAad/UpdateData/Final/CheckTag`,
already present in the pinned pkg wolfssl).

- **Crypto**: identical to manifest encryption — same embedded device key
  (`SUIT_ENC_SEC` → `suit_enc_seckey.h`), same recipient (X25519 ECDH-ES +
  HKDF-256 by default; ML-KEM-768/1024 via the existing compile-time
  dispatch), same CEK derivation and Enc_structure AAD, byte-exact CBOR
  rules. The container/CEK primitives were refactored out of `decrypt.c`
  into a shared internal header rather than duplicated.
- **Container**: COSE_Encrypt with **detached ciphertext** (RFC 9052 §5.1):
  `payload.bin.enc` = self-delimiting CBOR header (`ciphertext` slot =
  `null`; 74 B for X25519, ~1.1/1.6 KB for ML-KEM) `|| ciphertext
  (plaintext-length) || 16 B Poly1305 tag`. Wire format details:
  `firmware-encryption/README.md`.
- **Composition**: the manifest's image-digest/size stay computed over the
  **plaintext** (gen_manifest's `file` key), only the URI points at the
  `.enc` (`--enc-suffix`). Decrypt-then-verify: the signed digest over the
  stored plaintext remains the authenticity anchor; the AEAD tag adds
  early tamper rejection — a stream that fails the tag is never finalized
  (`suit_storage_finish()` never runs, nothing is installed).
- **Streaming state machine** (ported from the standalone example): chunks
  accumulate in a static header buffer until the header parses (it spans
  64-byte CoAP blocks; truncated CBOR fails cleanly → retry), then CEK +
  AAD are set up once and each chunk is decrypted **in place** and
  forwarded to the unchanged storage helper with **plaintext offsets**.
  The 16 most recent bytes are always withheld (trailing-tag lag) since
  the tag's position is only known at end-of-stream.
- **Pass-through**: payloads not starting with CBOR tag 96 (`0xd8 0x60`)
  are forwarded byte-identically, so plaintext payloads keep working on an
  encryption-capable firmware (same contract as manifest encryption).

## Opt-out contract

On by default; `SUIT_FIRMWARE_ENCRYPT=0` opts out.

| Side | `SUIT_FIRMWARE_ENCRYPT=1` (default) | `=0` |
|---|---|---|
| App build | `USEMODULE += suit_firmware_encrypt` (implies `suit_manifest_encrypt` — shared crypto + device key, even with `SUIT_MANIFEST_ENCRYPT=0`) | wrapper + header buffer absent (−1,140 B text / −392 B RAM on samr21; −2 KB / −448 B on native64) |
| Device runtime | encrypted payloads stream-decrypted; plaintext payloads pass through | plaintext only; an encrypted payload fails the normal size check (`Image beyond size`) |
| Host publish | `suit/publish` auto-encrypts payloads (`%.enc` rule) and generates manifests with `--enc-suffix .enc`; manual flow: `firmware-encryption/encrypt_firmware.py --no-headers --key <device key>` | unchanged pipeline |

## Changed / added files

| File | Change |
|---|---|
| `sys/suit/encrypt/payload_decrypt.c` (new) | The streaming decryptor: header reassembly/retry-parse, in-place chunk decrypt, tag-lag buffer, pass-through, `suit_payload_decrypt_start()/_helper()` |
| `sys/include/suit/firmware_encrypt.h` (new) | Public API of the wrapper callback |
| `sys/suit/encrypt/encrypt_internal.h` (new) | Shared internals: `cose_encrypt_msg_t`, `suit_cose_encrypt_parse()` (attached **or** detached, returns encoded length), `suit_cose_derive_cek()`, `suit_cose_build_enc_structure()` |
| `sys/suit/encrypt/decrypt.c` | Parser rewritten as a full-unwind (drain + leave every container) that handles both ciphertext forms and yields the exact header length; shared functions un-static'd; manifest path rejects detached containers; behavior otherwise unchanged |
| `sys/suit/handlers_command_seq.c` | `_dtv_fetch()`: with the module, `suit_payload_decrypt_helper` wraps `_storage_helper` for the CoAP and VFS transports |
| `sys/include/suit/pq_scratch.h` | Union extended: `{ mldsa | { mlkem, fw_hdr } }` — the payload header buffer may be live *with* the KEM decaps state (the KEM ct lives in it) but never with the ML-DSA verify state; also hosts the `SUIT_FW_ENC_HDR_LEN` sizing (192 / 1216 / 1696 B) |
| `sys/riotboot/flashwrite.c` | **Upstream bug fix** found during samr21 bring-up: RAW-mode `riotboot_flashwrite_putbytes()` flashed each full internal buffer at the *input segment's* position instead of the buffer-aligned block start — only correct for callers feeding buffer-aligned chunks (the plain CoAP path, by accident). The decryptor's chunks are shifted by the stripped 74 B COSE header, so blocks landed at wrong/unaligned addresses: AEAD tag OK, flash content garbled, `Verifying image digest` → `res=-7`. Fix is identity for aligned callers (`buffer_pos == 0`) |
| `makefiles/pseudomodules.inc.mk` | `PSEUDOMODULES += suit_firmware_encrypt` (source rides in the `suit_manifest_encrypt` module dir) |
| `sys/suit/Makefile.dep` | `suit_firmware_encrypt` → `suit_manifest_encrypt` |
| `makefiles/suit.base.inc.mk` | `SUIT_FIRMWARE_ENCRYPT ?= 1` + host-flow note (no new keys: same `SUIT_ENC_SEC`) |
| `makefiles/suit.inc.mk` | Publish automation when the flag is on: `%.enc` pattern rule (encrypt_firmware.py), `--enc-suffix .enc` in the gen_manifest call, `suit/publish` ships `.enc` payloads instead of plaintext |
| `dist/tools/suit/gen_manifest.py` | `--enc-suffix`: appends to the URI basename while digest/size stay over the plaintext file |
| `examples/advanced/suit_update/Makefile` | `SUIT_FIRMWARE_ENCRYPT ?= 1` → module selection; no `SUIT_MANIFEST_BUFSIZE` impact (the payload streams past the manifest buffer). Also: the ML-DSA builds' `SUIT_WORKER_STACKSIZE` handling is now scoped to non-native boards and unified with the ML-KEM override below (see gotchas) |
| `pkg/wolfssl/include/user_settings.h` | **RAM-feasibility bug fix** (2026-07-20, see gotchas): `MODULE_WOLFCRYPT_MLKEM` block gains `WOLFSSL_NO_MALLOC` + `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` + `WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM` — moves wolfCrypt's ML-KEM scratch buffers from an unconditional multi-KB heap `XMALLOC` (invisible to link-time RAM analysis, fails outright on a newlib heap this small) to a bounded, `-fstack-usage`-measured stack allocation |
| `examples/advanced/suit_update/Makefile` (stack sizing) | `SUIT_WORKER_STACKSIZE` consolidated to one emission point: `?= 4096` for ML-DSA, unconditionally raised to `9216` (measured peak, ~8.5-9KB) whenever `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-%` is selected, regardless of signing algorithm — the old scheme only applied the ML-DSA default (an insufficient 4096) and never touched Ed25519+ML-KEM builds at all |
| `examples/advanced/suit_update/firmware-encryption/` (new, plan Step 1) | Standalone interop example (Python encrypt ↔ wolfCrypt *streaming* decrypt in 64 B chunks) + wire-format README; `encrypt_firmware.py` doubles as the host-side tool, auto-detecting X25519 vs ML-KEM device keys |

## samr21-xpro feasibility — authoritative table (corrected 2026-07-20)

**Supersedes every ML-KEM samr21 number previously published** (in this
file's earlier revision and in `MLKEM_ENCRYPTION_PLAN.md`/`_CHANGES.md`)
— those were link-time-only (`arm-none-eabi-size`/`nm`) and never
accounted for wolfCrypt's runtime heap/stack use, which doesn't show up
in `.data`/`.bss`. See the gotchas section for the full story; this table
is the result *after* the `WOLFSSL_NO_MALLOC` fix and the corrected
9,216 B worker stack for ML-KEM builds.

`BOARD=samr21-xpro make clean all` per combo; RAM = data+bss of
`suit_update.elf` vs 32,768 B; ❌ = `.bss` overflow of `slot0.elf`
(link-time — now a trustworthy signal, not a false negative).

| Signing × Encryption (firmware + manifest enc on) | Result |
|---|---|
| Ed25519 + X25519 | ✅ 21,552 B RAM (**11.2 KB spare**); with `SUIT_FIRMWARE_ENCRYPT=0`: 21,160 → the decryptor costs **+392 B RAM / +1,140 B text** |
| ML-DSA-44 + X25519 | ✅ 31,280 B RAM (**1,488 B spare**) |
| ML-DSA-65 + X25519 | ❌ overflow **308 B** (baseline had 88 B spare — build Example B with `SUIT_FIRMWARE_ENCRYPT=0`) |
| ML-DSA-87 + anything | ❌ (already overflows without any encryption) |
| **Ed25519 + ML-KEM-768, manifest only** | ✅ 29,328 B RAM (**3,440 B spare**) |
| **Ed25519 + ML-KEM-768, manifest + payload** | ✅ 30,736 B RAM (**2,032 B spare**) |
| **Ed25519 + ML-KEM-1024, manifest + payload** | ✅ 32,720 B RAM (**48 B spare — essentially zero margin, do not build production images on this combo without re-verifying after any code change**) |
| **ML-DSA-44 + ML-KEM-768, manifest only (full PQ)** | ❌ **overflows 3,364 B** (previously miscounted as "1,160 B spare") |
| **ML-DSA-44 + ML-KEM-768, manifest + payload (full PQ)** | ❌ **overflows 3,556 B** (previously miscounted as "968 B spare") — this is the exact combo that produced `CEK derivation failed: -125` on real hardware before this fix, and would corrupt `.bss` at runtime even after the heap fix if the worker stack weren't also corrected |
| ML-DSA-44/65 + ML-KEM-1024, any | ❌ (strictly worse than the 768 rows above) |

**Practical upshot: the full-PQ combo (post-quantum signature AND
post-quantum encryption together) does not fit on samr21-xpro's 32 KB
RAM, full stop** — not a tuning problem, ~3.3-3.6 KB short even with
every available wolfCrypt memory-reduction flag enabled. Post-quantum
*encryption* alone (classical Ed25519 signature + ML-KEM-768/1024
manifest/payload encryption) does fit and is the recommended samr21 PQ
demo instead — see `SAMR21_EXAMPLES.md`'s Variant cookbook, recipe 7.

The ML-KEM payload header buffer (1,216/1,696 B) still overlays the idle
ML-DSA verify state via the `suit_pq_scratch` union in ML-DSA builds (that
part of the earlier RAM work was real and correct) — it just isn't enough
on its own once the stack requirement is accounted for.

## Verification (native64, 2026-07-19)

| Case | Result |
|---|---|
| Encrypted payload via `suit fetch file:///nvm0/...` (VFS, 308 B payload, header straddling the first 128 B chunk) | `decrypting payload (header 74 bytes)` → `payload decrypted (308 bytes)` → digest verified → installed |
| Encrypted payload over CoAP blockwise (64 B blocks, 1,305 B payload, tap networking + aiocoap-fileserver) | full update successful, `storage_content` shows plaintext |
| Tampered ciphertext byte in the `.enc` | `payload authentication failed` **before** `Finalizing payload store`; fetch aborts, nothing installed |
| Plaintext payload on encryption-capable firmware | `payload not encrypted, passing through` → normal update |
| `SUIT_FIRMWARE_ENCRYPT=0` build | symbol absent from image; plaintext update OK; encrypted payload rejected with `Image beyond size` (398 B stream vs 272 B image-size) |
| Host tool key auto-detection | X25519 / ML-KEM-768 / ML-KEM-1024 device keys all self-test OK |
| **Full-PQ E2E on native64** (ML-DSA-44 signature + ML-KEM-768 encrypted manifest **and** encrypted payload, 2026-07-20) | `decrypting payload (header 1126 bytes)` → `payload decrypted (1048 bytes)` → digest verified → installed. **native only** — this combo does NOT fit samr21-xpro's RAM (see the feasibility table); native's effectively-unlimited stack masked that until real hardware bring-up hit it |
| Standalone example | streaming MATCH in 64 B chunks (12.8 KB payload); tampered ciphertext / tag / nonce all rejected |

## Gotchas / lessons learned

- **wolfCrypt ML-KEM heap-allocates regardless of static RAM headroom —
  link-time "measured" RAM feasibility for ML-KEM was systematically
  wrong** (found on real samr21-xpro hardware, 2026-07-20, the biggest
  finding of this bring-up round). Symptom: `suit: manifest CEK
  derivation failed: -125` (wolfCrypt `MEMORY_E`) on the ML-DSA-44 +
  ML-KEM-768 combo — despite that exact combo being previously "measured"
  at 968-1,160 B RAM spare via `arm-none-eabi-size`/`nm`. Cause: without
  `WOLFSSL_NO_MALLOC`, `wc_MlKemKey_MakeKeyWithRandom()` unconditionally
  `XMALLOC`s a scratch buffer up to `(k+1)*k*MLKEM_N*sizeof(sword16)` =
  6,144 B (k=3, ML-KEM-768) from the C heap — code that runs at
  *runtime*, so it is invisible to any static `.data`/`.bss` measurement.
  The newlib heap (a few hundred B to a few KB depending on other tuning)
  can never satisfy that in one call. **The lesson generalizes: static
  link-time RAM analysis only bounds `.data`+`.bss`+the declared stack
  array — any code path that calls `malloc`/`XMALLOC` needs runtime
  verification, full stop, no matter how much "spare" `size` reports.**
  Fix: `WOLFSSL_NO_MALLOC` + `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` +
  `WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM` in `pkg/wolfssl/include/
  user_settings.h` move the buffers to the stack instead — bounded and
  measurable via `-fstack-usage`, which is how the real number (~8.5-9KB
  peak, `wc_MlKemKey_Decapsulate` → `mlkemkey_decapsulate` →
  `mlkemkey_encapsulate`, the last one alone consuming 4,664 B per its
  `.su` output) was obtained instead of guessed. That number is roughly
  **double** the 4 KB worker stack this feature was previously built and
  "verified" with — meaning every earlier samr21 ML-KEM run that
  happened to link would, on real hardware, have silently overflowed the
  worker stack into adjacent `.bss` (no MPU on Cortex-M0+) rather than
  failing cleanly. The app Makefile now forces
  `SUIT_WORKER_STACKSIZE=9216` whenever `SUIT_MANIFEST_ENCRYPT_ALGO=
  ml-kem-%` is selected (any signing algorithm, any board except native),
  turning an infeasible combo into a loud, honest link-time `.bss`
  overflow instead of a silent field failure. See the corrected
  feasibility table above and `MLKEM_ENCRYPTION_CHANGES.md`'s correction
  section (that document's original matrix is superseded by this one).
- **riotboot flashwrite silently assumes buffer-aligned chunks** (found on
  real samr21 hardware, 2026-07-20): first hardware run decrypted the full
  104,404 B image (tag OK — the streaming crypto worked first try on
  hardware) but failed the digest with `Erasing bad payload` / `res=-7`.
  Cause: RAW-mode `riotboot_flashwrite_putbytes()` wrote each filled
  buffer to the *segment* position rather than the aligned block start —
  harmless for the 64 B-aligned plaintext CoAP flow, fatal once the
  decryptor's forwarded chunks are shifted by the stripped COSE header
  (38 B first chunk). One-line fix in `sys/riotboot/flashwrite.c`;
  diagnosis shortcut for the future: tag passes + digest fails + host
  `sha256(decrypt(.enc)) == manifest digest` ⇒ the storage write path,
  not the crypto.
- **The 4 KB ML-DSA worker stack overflows on native** — found the hard
  way as *nondeterministic* Poly1305 tag mismatches on correct plaintext
  in the full-PQ build: `SUIT_WORKER_STACKSIZE=4096` (set for all ML-DSA
  builds to fit samr21's 32 KB RAM) is far too small for x86-64 stack
  frames once the payload path nests the ML-KEM decapsulation inside the
  transport callback (worker → `suit_parse` → fetch → vfs/coap cb →
  `derive_cek`), and the overflow silently corrupts adjacent `.bss` —
  here the static AEAD state. The override is now scoped to non-native
  boards (native uses the 3×`THREAD_STACKSIZE_LARGE` default). Sibling of
  the `MLDSA_HARDWARE_FIXES.md` stack lesson, in the other direction. On
  samr21 the same deeper call chain runs within the 4 KB Cortex-M stack —
  re-measure the watermark with `ps` during hardware bring-up.
- **nanocbor can't skip tags / needs drained children**: `nanocbor_skip()`
  does not descend into tagged items, and `nanocbor_leave_container()`
  positions the parent from the *child's* cursor — the only way to learn
  the exact COSE header length (where the ciphertext stream starts) is to
  walk and drain every nested container. This full unwind doubles as the
  completeness check for retry-parsing a header that arrives split across
  transport chunks.
- **Trailing-tag lag**: with detached AEAD content the tag is the last
  16 bytes of the stream, at a position only known at EOF — the decryptor
  must permanently withhold the 16 most recent bytes. Off-by-one here
  corrupts the final block *and* breaks authentication; the standalone
  example exercises exactly this (chunk sizes 64, header 74 → first
  ciphertext bytes arrive inside the header buffer).
- **Storage helper contract**: the wrapper forwards *plaintext offsets*,
  so `_storage_helper`'s image-size checks and the digest verification
  work unchanged; the final `more=0` call is only forwarded **after**
  `CheckTag` passes, so a bad stream never reaches
  `suit_storage_finish()`. (A failed fetch can still leave a decrypted
  prefix in the inactive slot/region — same upstream TODO as the
  plaintext flow.)
- **RAM storage regions on native are 2 KB** — that limit applies to the
  *plaintext* size; a bigger test payload fails at
  `Unable to start storage backend` (res=-50), before any decryption.
- **Per-device artifacts**: like encrypted manifests, one `.enc` payload
  per device key. `suit/publish` publishes only the `.enc` files when the
  flag is on — publishing the plaintext next to them would defeat the
  purpose.
- Prototype caveats inherited from manifest encryption: device key
  embedded in the image, no sender authentication from ECDH-ES/KEM alone
  (the signed manifest is the anchor), not security-audited.
