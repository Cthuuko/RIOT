# Findings — what was measured, what was proven, and when

The results half of this project. [GOTCHAS.md](GOTCHAS.md) tells you how to
avoid failures; this file tells you *which configurations are possible at all*
and what the numbers behind that are.

Per-file implementation detail lives in the deep-dive documents linked at the
bottom; this is the curated summary the device matrices are derived from.

---

## 1. Headline results

1. **Post-quantum SUIT updates work on real constrained hardware.**
   ML-DSA-44 and ML-DSA-65 manifest signing verify on-device on a
   samr21-xpro (Cortex-M0+, 48 MHz, 32 KB RAM) and complete full OTA cycles.
   *(2026-07-18)*

2. **Post-quantum *encryption* also works, but not together with post-quantum
   signing on 32 KB.** Ed25519 + ML-KEM-768 fits the samr21 with 2.0 KB spare.
   **ML-DSA + ML-KEM together does not fit — by ~3.5 KB — and is not a tuning
   problem.** *(corrected 2026-07-20)*

3. **The full post-quantum combination is real, on a roomier board.**
   ML-DSA-65 signing + ML-KEM-768 manifest encryption + ML-KEM-768 payload
   encryption completed a full OTA cycle on an nRF52840 Dongle (256 KB RAM),
   including reboot into the new slot. *(2026-07-22)*

4. **Firmware payloads can be decrypted while streaming into flash**, with no
   need to buffer the image — verified on both native64 and the dongle.

5. **Link-time RAM analysis systematically lied to us.** The single most
   important methodological finding of the project — see §4.

---

## 2. Cost of post-quantum crypto

### Signatures (FIPS 204)

| | Ed25519 | ML-DSA-44 | ML-DSA-65 | ML-DSA-87 |
|---|---|---|---|---|
| Security category | classical | 2 | 3 | 5 |
| COSE algorithm ID | −8 | −48 | −49 | −50 |
| Public key | 32 B | 1312 B | 1952 B | 2592 B |
| Signature | 64 B | 2420 B | 3309 B | 4627 B |
| `SUIT_MANIFEST_BUFSIZE` needed | 640 | 3072 | 3840 | 5376 |
| Verify time on 48 MHz M0+ | «1 s | sub-second | noticeable pause | n/a (does not fit) |

A signature grows **~50×** going from Ed25519 to ML-DSA-65, and the manifest
buffer with it. On a 32 KB device the buffer alone is >10 % of RAM.

### Key establishment (FIPS 203)

| Scheme | Container overhead per manifest |
|---|---|
| X25519 + HKDF + ChaCha20-Poly1305 | ~92 B |
| ML-KEM-768 | ~1144 B |
| ML-KEM-1024 | ~1696 B |

Confirmed on hardware: a 4892 B encrypted manifest decrypted to 3748 B
plaintext — a 1144 B ML-KEM-768 overhead, matching exactly.

The real cost is not the wire overhead but the **working memory**:
wolfCrypt's ML-KEM decapsulation chain peaks at **~8.5–9 KB of stack**
(`wc_MlKemKey_Decapsulate` → `mlkemkey_decapsulate` → `mlkemkey_encapsulate`,
the last alone consuming 4,664 B per its `-fstack-usage` output).

---

## 3. Feasibility by board

### 3.1 samr21-xpro — 32 KB RAM (the constrained case)

All figures link-level, out of 32,768 B, **corrected 2026-07-20** (see §4) and
**re-confirmed 2026-07-23** by rebuilding all 20 combinations from a clean
`BINDIR` — every previously published number reproduced exactly.

These are **ethos-mode** figures. In 802.15.4 radio mode (`USE_ETHOS=0`) the
budget shifts: ~1.6 KB less RAM used but ~12 KB more ROM, and since a samr21
riotboot slot is only 128,768 B the **binding constraint becomes ROM**. Three
combinations that fit over ethos (ML-DSA-44 + X25519, ML-DSA-44 + X25519 +
payload, ML-DSA-65 + X25519) **overflow ROM** wirelessly. Full radio-mode
matrix: [DEVICE_802154_PI.md](DEVICE_802154_PI.md#combination-matrix--radio-mode).

| Signature | Manifest enc | Payload enc | RAM used | Verdict |
|---|---|---|---|---|
| Ed25519 | — | — | ~20,600 B | ✅ verified on hardware |
| Ed25519 | X25519 | — | 21,160 B (11.6 KB spare) | ✅ verified on hardware 2026-07-19 |
| Ed25519 | X25519 | X25519 | 21,552 B (11.2 KB spare) | ✅ links |
| Ed25519 | ML-KEM-768 | ML-KEM-768 | 30,736 B (**2,032 B spare**) | ✅ links — best PQ-encryption option here |
| Ed25519 | ML-KEM-1024 | ML-KEM-1024 | 32,720 B (**48 B spare**) | ✅ links, zero margin — one-off experiment only |
| ML-DSA-44 | — | — | 30,760 B (2,008 B spare) | ✅ verified on hardware |
| ML-DSA-44 | X25519 | X25519 | 31,280 B (1,488 B spare) | ✅ links |
| ML-DSA-65 | — | — | 32,552 B (~216 B spare) | ✅ verified on hardware 2026-07-18 |
| ML-DSA-65 | X25519 | — | 32,680 B (**88 B spare**) | ✅ links, runtime stability unproven |
| ML-DSA-65 | X25519 | X25519 | overflow 308 B | ❌ — build with `SUIT_FIRMWARE_ENCRYPT=0` |
| ML-DSA-44 | ML-KEM-768 | — | overflow 3,364 B | ❌ **confirmed infeasible** |
| ML-DSA-44 | ML-KEM-768 | ML-KEM-768 | overflow 3,556 B | ❌ **confirmed infeasible** |
| ML-DSA-65 | ML-KEM-768 | ML-KEM-768 | overflow 6,324 B | ❌ **confirmed infeasible** |
| ML-DSA-87 | — | — | overflow 3,628 B | ❌ never links |
| ML-DSA-87 | X25519 | — | overflow 3,756 B | ❌ never links |
| ML-DSA-87 | X25519 | X25519 | overflow 4,148 B | ❌ never links |
| ML-DSA-87 | ML-KEM-1024 | ML-KEM-1024 | overflow 10,644 B | ❌ never links |

The decryptor itself costs **+392 B RAM / +1,140 B text**. Manifest encryption
costs ~17 KB of *flash* on native64 when enabled.

**Conclusion for this board:** you can have a post-quantum *signature*, or
post-quantum *encryption*, but not both. `Ed25519 + ML-KEM-768` is the
recommended PQ-encryption demo; `ML-DSA-44` (or `-65` with
`SUIT_FIRMWARE_ENCRYPT=0`) is the recommended PQ-signature demo.

### 3.2 nRF52840 Dongle — 256 KB RAM (the roomy case)

RAM is not the constraint. **All 20 combinations were built from a clean
`BINDIR` on 2026-07-23 and every one links**, in both `cdc-ecm` and
`DONGLE_NETIF=radio` mode. Worst case is ML-DSA-87 + ML-KEM-1024 at 47,236 B —
**18 %** of the 262,144 B budget. If a build here overflows, suspect a
configuration mistake, not the board.

| Combination | Status |
|---|---|
| Ed25519 + plaintext manifest + X25519 payload | ✅ **full OTA verified 2026-07-22** — `payload decrypted (108028 bytes)`, `Running from slot 1` |
| **ML-DSA-65 + ML-KEM-768 manifest + ML-KEM-768 payload (full PQ)** | ✅ **full OTA verified 2026-07-22** — manifest 4892 B → 3748 B, payload header 1126 B, `payload decrypted (124188 bytes)`, `Running from slot 1` |
| ML-DSA-87 signing | 🔨 **builds** (40,220 B RAM), runtime not yet exercised |
| ML-DSA-87 + ML-KEM-1024 (max strength) | 🔨 **builds** (47,236 B RAM) |
| everything else | 🔨 **builds** — link confirmed 2026-07-23, runtime not individually run |

The dongle's reason to exist in this thesis: it demonstrates the
**category-5 signature** and the **full post-quantum combination** that the
samr21 physically cannot build.

### 3.3 native / native64

Effectively unconstrained (host RAM, large default thread stacks). All 20
combinations build (re-confirmed 2026-07-23), and the ones listed as ✅ in
[DEVICE_NATIVE.md](DEVICE_NATIVE.md#combination-matrix--native--native64) have
been exercised end to end, including the full-PQ combo that fails on samr21. Useful as the correctness reference; **not** a feasibility signal for
real hardware — precisely the trap described in §4.

Caveat: RAM storage regions on native are 2 KB, applied to the *plaintext*
size.

---

## 4. The wolfCrypt ML-KEM heap discovery

**The most consequential finding of the project, and the reason several
earlier numbers in this repository were wrong.**

### What happened

The ML-DSA-44 + ML-KEM-768 combination was "measured" at 968–1,160 B RAM
spare using `arm-none-eabi-size` and `nm`, and documented as feasible. On real
samr21 hardware it failed with:

```
suit: manifest CEK derivation failed: -125       # wolfCrypt MEMORY_E
```

### Why

Without `WOLFSSL_NO_MALLOC`, `wc_MlKemKey_MakeKeyWithRandom()` unconditionally
`XMALLOC`s a scratch buffer of `(k+1)·k·MLKEM_N·sizeof(sword16)` = **6,144 B**
(k=3, ML-KEM-768) from the C heap. That allocation happens at **runtime**, so
it is invisible to any static `.data`/`.bss` measurement. The newlib heap on
that board — a few hundred bytes to a few KB — can never satisfy it in one
call.

### The generalizable lesson

> **Static link-time RAM analysis only bounds `.data` + `.bss` + the declared
> stack array. Any code path that calls `malloc`/`XMALLOC` needs runtime
> verification, full stop — no matter how much "spare" `size` reports.**

### The fix, and what it revealed

`WOLFSSL_NO_MALLOC` + `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` +
`WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM` (`pkg/wolfssl/include/user_settings.h`)
move those buffers to the **stack**, where `-fstack-usage` can measure them:
**~8.5–9 KB peak**.

That is roughly **double** the 4 KB worker stack the feature had previously
been built and "verified" with. Every earlier samr21 ML-KEM build that
happened to link would, on real hardware, have silently overflowed the worker
stack into adjacent `.bss` (no MPU on Cortex-M0+) rather than failing cleanly.

The app `Makefile` now forces `SUIT_WORKER_STACKSIZE=9216` for any
`ml-kem-%` build on any non-native board. **Re-measuring with the correct
stack size is what turned the full-PQ combo from "✅ 968 B spare" into
"❌ overflows by 3,556 B."** It never actually fit; the previous measurement
was simply measuring the wrong thing.

---

## 5. Other hardware-only findings

None of these reproduced on native64 — they were all found by running on real
boards.

### ML-DSA on samr21: three fixes (2026-07-18)

1. **Manifest buffer too small.** `SUIT_MANIFEST_BUFSIZE` defaults to 640 B —
   fine for Ed25519, but an ML-DSA-65 envelope is ~3.7 KB, and both transports
   reject content that does not fit. Symptom:
   `suit_worker: error getting manifest`. Fixed with a per-level buffer size.

2. **A 10 KB `MlDsaKey` on the worker stack.** With the
   `WOLFSSL_MLDSA_VERIFY_NO_MALLOC` + `_SMALL_MEM` build (the only one that
   fits), wolfCrypt embeds *every* verify work buffer inside `struct MlDsaKey`.
   Declared as a stack local it produced a **12,364-byte stack frame** on a
   ≤12 KB stack. With no MPU, the overflow silently corrupted adjacent `.bss`
   — including the manifest buffer holding the signature — so a **genuinely
   valid signature failed verification** (`SIG_VERIFY_E`, -229) while the
   identical triple verified on native64.
   Fixed by making the key `static` (safe: SUIT verification only ever runs on
   the single `suit_worker` thread). Stack frame after the fix: **52 bytes**.

3. **RAM budget.** `WOLFSSL_MLDSA_ASSIGN_KEY` turns `MlDsaKey.p` into a
   `const byte *` pointer instead of a 1952-byte embedded copy (valid because
   the trusted key is a `const` array in flash) — saves ~2 KB. Plus
   `SUIT_WORKER_STACKSIZE=4096` and the tightened manifest buffer. Result:
   106,012 text / 32,552 RAM — **~216 B spare**, stable through repeated
   updates.

### riotboot flashwrite alignment bug (2026-07-20)

The first encrypted-payload hardware run decrypted the full 104,404 B image
with a valid AEAD tag, then failed the digest (`Erasing bad payload`,
`res=-7`). Cause: RAW-mode `riotboot_flashwrite_putbytes()` wrote each filled
buffer at the *input-segment* position rather than the aligned block start —
correct only for buffer-aligned chunks, and the decryptor's header-stripped
stream begins with a 38 B chunk. One-line fix in
`sys/riotboot/flashwrite.c`. **The crypto worked first try; the storage layer
did not.**

### nRF52840 Dongle bring-up (2026-07-21 → 07-22)

- A plain `make flash` produces a **monolithic** image with no riotboot slots;
  every update dies at `res=-50`. The working path is a two-stage
  `riotboot_dfu` install — RIOT's own DFU bootloader at `ROM_OFFSET=0x1000`,
  installed *through* the Nordic DFU, then slot flashing with `dfu-util`.
  **No SWD probe required.**
- Flash geometry was `PROGRAMMER`-dependent, so a `dfu-util`-flashed slot and
  a `nrfutil`-published manifest disagreed on `SLOT1_OFFSET`
  (`0x82000` vs `0x71800`) → `res=-4`, `offset does not match`. Fixed by
  pinning `ROM_OFFSET`/`ROM_LEN` in the app Makefile.
- The URL path buffer is actually **128 bytes** for SUIT apps
  (`makefiles/suit.inc.mk` exports `-DCONFIG_SOCK_URLPATH_MAXLEN=128`), not
  the 64 quoted in earlier notes. A 64-char dongle path processed correctly.

### Worker stack direction reversal (native)

The 4 KB worker stack chosen to fit samr21 is **too small for native**, where
x86-64 frames are larger and the payload path nests ML-KEM decapsulation
inside the transport callback. It corrupted the static AEAD state, producing
*nondeterministic* Poly1305 tag failures on correct plaintext. The override
is now scoped to non-native boards. A sibling of the ML-DSA lesson, in the
opposite direction.

---

## 6. Design notes worth knowing

- **Sign-then-encrypt on the host, decrypt-then-verify on the device.** The
  signature always covers plaintext, so confidentiality never weakens
  authenticity.
- **Manifest decryption is in-place.** wolfCrypt's ChaCha20-Poly1305 supports
  `out == in`; plaintext lands inside the manifest buffer at the ciphertext's
  offset and `suit_parse()` is handed an interior pointer — no memmove, no
  second buffer.
- **Payload decryption is streaming.** A wrapper in front of `_storage_helper`
  in `sys/suit/handlers_command_seq.c` runs wolfCrypt's incremental AEAD with
  a 16-byte trailing-tag lag; the manifest's digest and size stay over the
  *plaintext* (`gen_manifest.py --enc-suffix .enc`). The image is never
  buffered whole.
- **`suit_pq_scratch`** (`sys/include/suit/pq_scratch.h` + libcose patch 0003)
  makes the never-concurrent ML-DSA verify state and the `MlKemKey` share one
  static allocation. The saving is real — it just is not enough to make the
  full-PQ combo fit 32 KB.
- **Compile-time algorithm dispatch.** ML-KEM uses private-use COSE algorithm
  IDs −70768 / −70769; the device accepts exactly the scheme it was built for
  and logs `recipient alg X != built-in Y` otherwise.
- **The device key is a 64 B FIPS 203 seed**, re-expanded deterministically at
  runtime — not the 2.4/3.2 KB expanded decapsulation key.

---

## 7. Known limitations and open items

- **ML-DSA-87 has never run on real hardware.** It cannot fit the samr21; the
  dongle should accommodate it but this has not been exercised.
- **Manifest encryption is not automated** in `suit/publish` — it is a manual
  post-publish step.
- **Tamper rejection is native64-verified only** for several combinations;
  hardware confirmation exists for the ML-KEM implicit-rejection path
  (`-213`) but not for every variant.
- **A pre-existing, unrelated failure** affects the thesis manifest layout
  (`coaproot/payload.bin:0:ram:0` via `Makefile.thesis.suit.manifest`) on
  native: all three ML-DSA levels fail *after* successful signature
  verification at the component/common-sequence stage. A baseline test with
  all multi-level changes stashed showed the original 65-only code fails
  identically — **not a regression**, and signature verification (the point of
  the integration) succeeds.
- **A failed fetch can leave a decrypted prefix** in the inactive slot — the
  same upstream TODO as the plaintext flow.
- **Not security-audited**, and the device private key is embedded in the
  firmware image.

---

## 8. Deep dives

| Document | Content |
|---|---|
| [MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md) | the three samr21 ML-DSA-65 fixes, with disassembly-level diagnosis |
| [MLDSA_MULTILEVEL_CHANGES.md](MLDSA_MULTILEVEL_CHANGES.md) | generalizing to 44/65/87; per-file changes; native64 test matrix |
| [MANIFEST_ENCRYPTION_PLAN.md](MANIFEST_ENCRYPTION_PLAN.md) · [MANIFEST_ENCRYPTION_CHANGES.md](MANIFEST_ENCRYPTION_CHANGES.md) | X25519 manifest encryption: wire format, opt-out contract, per-file changes |
| [MLKEM_ENCRYPTION_PLAN.md](MLKEM_ENCRYPTION_PLAN.md) · [MLKEM_ENCRYPTION_CHANGES.md](MLKEM_ENCRYPTION_CHANGES.md) | ML-KEM variant, selection contract, original 12-combo matrix (superseded by §3.1) |
| [FIRMWARE_ENCRYPTION_PLAN.md](FIRMWARE_ENCRYPTION_PLAN.md) · [FIRMWARE_ENCRYPTION_CHANGES.md](FIRMWARE_ENCRYPTION_CHANGES.md) | streaming payload encryption; the authoritative re-measured tables |
| [mlkem-feasibility-matrix-raw.txt](mlkem-feasibility-matrix-raw.txt) | raw `size`/ld output of the 12 signing × encryption samr21 builds |
| [manifest-encryption/README.md](manifest-encryption/README.md) · [manifest-encryption-mlkem/README.md](manifest-encryption-mlkem/README.md) · [firmware-encryption/README.md](firmware-encryption/README.md) | standalone host-only interop demos and COSE wire formats |

> Where an older document's feasibility numbers disagree with §3.1, **§3.1
> wins** — it reflects the 2026-07-20 correction described in §4.
