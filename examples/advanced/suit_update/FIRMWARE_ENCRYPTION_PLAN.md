# Firmware image encryption (ChaCha20-Poly1305) — implementation plan

Living plan document (sibling of `MANIFEST_ENCRYPTION_PLAN.md` /
`MLKEM_ENCRYPTION_PLAN.md`, whose crypto pipeline this reuses) — future
sessions resume from the first unchecked step.

## Context

The SUIT workflow now has manifest confidentiality (X25519 or ML-KEM
recipient + ChaCha20-Poly1305, decrypted in `sys/suit/encrypt/decrypt.c`
before `suit_parse()`), but the **firmware image itself still travels in
plaintext**: the manifest's URI points at the raw `payload.bin` /
`slotN.riot.bin`, which anyone on the path can read. Goal: encrypt the
firmware payload on the host with ChaCha20-Poly1305 and decrypt it
on-device **while it streams in** — the image (up to ~120 KB on real
boards) never fits in RAM, so unlike the manifest there is no
buffer-then-decrypt option.

## Design

Reuse the manifest-encryption crypto wholesale — same device key
(`SUIT_ENC_SEC` → embedded `suit_enc_seckey.h`), same recipient structure
(X25519 ECDH-ES + HKDF-256 by default, ML-KEM-768/1024 via the existing
compile-time dispatch), same CEK derivation (HKDF-SHA256 with the
byte-exact COSE_KDF_Context) and AAD (Enc_structure). Only the container
changes: the ciphertext is **detached** (RFC 9052 §5.1 detached content),
because it must stream past the CBOR parser:

```
payload.bin.enc :=
  96([                      / COSE_Encrypt, CBOR tag 96                 /
    << {1: 24} >>,          / protected: alg = ChaCha20/Poly1305        /
    {5: h'<nonce 12B>'},    / unprotected: IV                           /
    null,                   / ciphertext DETACHED (streams after this)  /
    [[                      / one recipient — identical to the manifest /
      << {1: -25} >>,       /   container (or -70768/-70769 for ML-KEM) /
      {-1: {1:1, -1:4, -2: h'<ephemeral pub 32B>'}},
      h''
    ]]
  ])
  || ciphertext (same length as the plaintext image)
  || Poly1305 tag (16 B)
```

- **Streaming decrypt on-device**: wolfCrypt's incremental AEAD API
  (`wc_ChaCha20Poly1305_Init/UpdateAad/UpdateData/Final` + `_CheckTag`,
  present in the pinned pkg wolfssl) processes each transport chunk in
  place. A 16-byte lag buffer withholds the trailing tag from decryption
  until the transfer ends (the tag position is only known at EOF).
- **Hook point**: a wrapper around `_storage_helper` in
  `sys/suit/handlers_command_seq.c`'s `_dtv_fetch()` — the single funnel
  both the CoAP blockwise and VFS transports already call with strictly
  sequential `(offset, buf, len, more)` chunks. The wrapper buffers/parses
  the COSE header (spans multiple 64-byte CoAP blocks; retry-parse until
  the self-delimiting CBOR is complete), derives the CEK, then forwards
  **plaintext chunks with plaintext offsets** to the unchanged storage
  helper. Tag verified *before* the final `more=0` forward, so a tampered
  stream never gets finalized/installed.
- **Manifest stays the security anchor**: `gen_manifest.py` keeps hashing
  the **plaintext** file for image-digest/size (suit-tool's `file` key)
  while the URI points at `<file>.enc` (new `--enc-suffix` option). The
  device-side digest check over the decrypted, stored image is untouched —
  decrypt-then-verify, exactly like the manifest composition. The AEAD tag
  adds early tamper rejection; authenticity still comes from the signed
  manifest.
- **Pass-through**: a payload that does not start with `0xd8 0x60` (CBOR
  tag 96) is forwarded unmodified, so an encryption-capable firmware still
  accepts plaintext payloads (same contract as the manifest feature).
- **Module**: pseudomodule `suit_firmware_encrypt` (depends on
  `suit_manifest_encrypt` for the shared parse/CEK code, which moves
  behind a small internal header in `sys/suit/encrypt/`). App flag
  `SUIT_FIRMWARE_ENCRYPT ?= 1` mirroring `SUIT_MANIFEST_ENCRYPT`, opt-out
  with `=0` (module + wrapper absent entirely).
- **Host tooling**: `firmware-encryption/encrypt_firmware.py` (standalone
  example + real tool, like the two `encrypt_manifest.py` siblings);
  `suit.inc.mk` gains a `%.enc` rule + publishes `.enc` payloads (and only
  those) when the flag is on; `gen_manifest.py --enc-suffix .enc`.

## Status checklist

- [x] Step 0: this plan saved to the repo (2026-07-19)
- [x] Step 1: standalone interop example
  `examples/advanced/suit_update/firmware-encryption/`
  (`encrypt_firmware.py`, `wolfcrypt-sample.c` doing *chunked streaming*
  decrypt with the incremental API + 16-byte tag lag, README) — verified
  2026-07-19: 12,828 B test payload streamed in 64 B chunks → MATCH
  (header 74 B, straddling the first chunk boundary); tampered
  ciphertext/tag/nonce all rejected at the tag check; 12.7 KB real-file
  input OK; Python self-test OK. Gotcha found: nanocbor's `skip` doesn't
  descend into tags and `leave_container` needs drained children — the
  header parser must fully unwind every container to learn the header
  length (recorded in the example README).
- [x] Step 2: samr21-xpro feasibility measured (2026-07-19, link-level
  `make clean all` per combo): decryptor costs +392 B RAM / +1,140 B
  text; Ed25519 and ML-DSA-44 (X25519) combos fit; ML-DSA-65 overflows
  by 308 B (documented: `SUIT_FIRMWARE_ENCRYPT=0` there). **⚠️ The
  original full-PQ number here ("968 B spare") was WRONG — corrected in
  Step 6 below after real hardware bring-up; see
  `FIRMWARE_ENCRYPTION_CHANGES.md`'s authoritative table.**
- [x] Step 3: device integration + host tooling (2026-07-19) — see
  `FIRMWARE_ENCRYPTION_CHANGES.md` for the per-file list; includes
  `suit/publish` automation (`%.enc` rule + `--enc-suffix`, verified
  against a real 122 KB samr21 slot image) and ML-KEM auto-detection in
  `encrypt_firmware.py`
- [x] Step 4: E2E on `BOARD=native` (2026-07-19/20) — encrypted payload
  over **CoAP** (64 B blocks) and **VFS**; tampered payload rejected
  before finalize; plaintext pass-through; opt-out build clean; **full-PQ
  E2E on native only** (ML-DSA-44 + ML-KEM-768 manifest *and* payload) —
  which exposed and fixed a pre-existing native worker-stack overflow
  (4 KB ML-DSA stack vs x86-64 frames; now scoped to non-native boards)
- [x] Step 5: docs, round 1 (2026-07-20) — `FIRMWARE_ENCRYPTION_CHANGES.md`,
  `NATIVE_SETUP.md` step 6c + log lines + opt-out note,
  `SAMR21_EXAMPLES.md` Example F (**unverified draft** + F.9 bring-up
  checklist), `CLAUDE.md` tables/gotchas
- [x] Step 6: real-hardware (samr21-xpro) bring-up, first attempt
  (2026-07-20) — hit `res=-7` digest failure on encrypted payload
  (**fixed**: `sys/riotboot/flashwrite.c` chunk-alignment bug), then a
  manifest-buffer-too-small error on the full-PQ combo (**fixed**:
  `SUIT_MANIFEST_BUFSIZE` recalculated from real `suit/publish` URI
  lengths), then a mute-shell heap exhaustion (**fixed**: pktbuf
  rebalance), then `CEK derivation failed: -125` on the full-PQ combo
  — this last one was **not a bug in this feature**, but a pre-existing
  wolfCrypt ML-KEM defect: unconditional multi-KB heap allocation,
  invisible to every prior link-time RAM measurement. **Fixed**
  (`WOLFSSL_NO_MALLOC` + stack-size correction, see
  `FIRMWARE_ENCRYPTION_CHANGES.md` gotchas) and **re-measured for real**:
  the full-PQ combo (any ML-DSA + any ML-KEM) does not fit samr21's
  32 KB RAM (~3.3-3.6 KB short, not tunable further); Ed25519 +
  ML-KEM-768/1024 does fit (2.0-3.4 KB spare) and is now the recommended
  samr21 PQ-encryption target. Outstanding: an actual OTA cycle on
  hardware with the now-corrected Ed25519+ML-KEM-768 build (F.9
  checklist), and the flashwrite-fix retest for the plain payload-only
  combo that surfaced it.
- [ ] Step 7: docs, round 2 (2026-07-20) — correct every previously
  "measured" ML-KEM samr21 number across
  `MLKEM_ENCRYPTION_PLAN.md`/`_CHANGES.md`, `SAMR21_EXAMPLES.md`'s
  cookbook and Example F, `CLAUDE.md`

## samr21-xpro feasibility (Step 2, review before implementing)

Baselines: measured matrix in `MLKEM_ENCRYPTION_PLAN.md`. Expected adds
from this feature (mostly .bss):

- `ChaChaPoly_Aead` streaming state ≈ 400 B + header buffer (~192 B
  X25519 / ~1.3 KB ML-KEM-768 / ~1.8 KB ML-KEM-1024, sized like the
  manifest-buffer bumps) + 16 B lag + bookkeeping → ~0.6 KB (X25519),
  ~1.8/2.3 KB (ML-KEM).
- Flash: header parsing + wrapper ≈ 1–2 KB (crypto code fully shared with
  the already-linked manifest decrypt).
- **MEASURED, corrected 2026-07-20** — see the authoritative table in
  `FIRMWARE_ENCRYPTION_CHANGES.md` (supersedes an earlier, wrong version
  of this section that never accounted for wolfCrypt's runtime ML-KEM
  heap/stack use — see that document's gotchas for the full story).
  Summary (RAM/32,768 B): Ed25519+X25519 ✅ 21,552 (11.2 KB spare; +392 B
  / +1,140 B text vs opt-out); ML-DSA-44+X25519 ✅ 31,280 (1,488 B
  spare); ML-DSA-65+X25519 ❌ −308 B (was 88 B spare →
  `SUIT_FIRMWARE_ENCRYPT=0` there); **Ed25519+ML-KEM-768 ✅ 30,736
  (2,032 B spare)**; Ed25519+ML-KEM-1024 ✅ 32,720 (48 B spare, near
  zero margin); **ML-DSA-44+ML-KEM-768 (full PQ) ❌ overflows 3,556 B**
  — does not fit, full stop, not a tuning problem. The payload header
  buffer still joins the `suit_pq_scratch` union as a *sibling* of
  `MlKemKey` in ML-DSA builds (the KEM ct being decapsulated lives
  inside the header buffer, so they must not overlap each other; both
  overlay the idle ML-DSA verify state) — that saving is real, it's just
  not enough once the ~8.5-9KB ML-KEM decapsulation stack requirement is
  correctly accounted for.

## Verification plan

Step 1 (standalone): Python self-test; C sample decrypts in 64-byte
chunks (proving header-spans-chunk and tag-lag logic) → MATCH; tamper
ciphertext / tag / header → all rejected.

Step 4 (native E2E, per `NATIVE_SETUP.md`): encrypted payload +
encrypted manifest full update (`storage_content` shows plaintext);
tampered `.enc` payload → fetch fails, nothing installed; plaintext
payload on encryption-capable firmware → pass-through; seqnr bumped
every round; `SUIT_FIRMWARE_ENCRYPT=0` build byte-identical behavior.
