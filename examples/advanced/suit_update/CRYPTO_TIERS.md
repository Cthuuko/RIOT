# Crypto tiers — Full Classical vs Hybrid vs Full PQC

Every other document in this project slices the results **by board**:
[FINDINGS.md](FINDINGS.md) §3 has the per-board RAM tables, each
`DEVICE_*.md` carries its own 20-row matrix. This document slices the *same*
measurements the other way — **by crypto tier** — to answer the one question
the thesis exists to answer:

> What does going post-quantum actually cost, and where does it stop being
> possible?

**No new measurements were taken for this document.** Every figure is cited
back to the file it was measured in. Where sources disagree,
[FINDINGS.md](FINDINGS.md) §3.1 wins (the 2026-07-20 correction).

---

## 1. What the three tiers are

The workflow has [three independent axes](GUIDE.md#3-the-three-axes)
(signature, manifest encryption, payload encryption), so "hybrid" is
ambiguous: it can mean a post-quantum *signature* with classical encryption,
or a classical signature with post-quantum *encryption*. Those two behave very
differently on constrained hardware, so **hybrid is presented as two
sub-tiers** throughout.

| | **Full Classical** | **Hybrid (PQ-enc)** | **Hybrid (PQ-sig)** | **Full PQC** |
|---|---|---|---|---|
| Signature | Ed25519 | Ed25519 | **ML-DSA-44** | **ML-DSA-65** |
| Manifest encryption | X25519 | **ML-KEM-768** | X25519 | **ML-KEM-768** |
| Payload encryption | X25519 | **ML-KEM-768** | X25519 | **ML-KEM-768** |
| Quantum-safe against forgery | ✗ | ✗ | ✓ | ✓ |
| Quantum-safe against decryption | ✗ | ✓ | ✗ | ✓ |
| samr21 matrix row | 4 | 6 | 9 | 15 |
| dongle matrix row | 4 | 6 | 10 | 19 |

Build invocation per tier (add `BOARD=…`; the same flags must be passed to
`suit/publish` — see [the matching rule](SETUP_COMMON.md#the-matching-rule)):

```bash
# Full Classical — these are the defaults
make -C examples/advanced/suit_update all

# Hybrid (PQ-enc)
SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  make -C examples/advanced/suit_update all

# Hybrid (PQ-sig)
SUIT_KEY_ALGO=ml-dsa-44 \
  make -C examples/advanced/suit_update all

# Full PQC
SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  make -C examples/advanced/suit_update all
```

> **"Hybrid" here means mixed axes**, not the IETF/NIST sense of a *composite*
> where a classical and a post-quantum signature are both applied and both
> must verify. Composite schemes are **not implemented** in this workflow.

### A second classical signature: ES256/384/512

The Full Classical column above uses Ed25519, but the workflow also supports
**ECDSA over the NIST curves** (`SUIT_KEY_ALGO=es256|es384|es512`, COSE
`-7`/`-35`/`-36`) — the signature algorithm the wider SUIT/COSE ecosystem
treats as the default. It is an interoperability option, not a stronger one:
it is equally broken by Shor's algorithm, so it belongs entirely inside the
classical tier. Measured cost on samr21 (encryption off): 100,800 B ROM for
ES256, rising to 105,208 B for ES512, with **RAM identical across all three
curves** at 21,368 B. See [FINDINGS.md](FINDINGS.md#classical-ecdsa-es256es384es512--the-interop-baseline).

---

## 2. Cryptographic artifact sizes

Signatures and keys, from [FINDINGS.md:41-48](FINDINGS.md) (mirrored in
[SETUP_COMMON.md §3.3](SETUP_COMMON.md)):

| | Full Classical | Hybrid (PQ-enc) | Hybrid (PQ-sig) | Full PQC |
|---|---|---|---|---|
| Signature algorithm | Ed25519 | Ed25519 | ML-DSA-44 | ML-DSA-65 |
| NIST security category | classical | classical | 2 | 3 |
| COSE algorithm ID | −8 | −8 | −48 | −49 |
| Public key (embedded in firmware) | 32 B | 32 B | **1312 B** | **1952 B** |
| Signature | 64 B | 64 B | **2420 B** | **3309 B** |
| `SUIT_MANIFEST_BUFSIZE` | 640 | 640 (+1216) | 3072 (+128) | 3840 (+1216) |

The signature grows **~38× (ML-DSA-44) to ~52× (ML-DSA-65)**, and the manifest
buffer must grow with it. On a 32 KB device the ML-DSA-65 buffer alone is over
10 % of total RAM — before any code.

Key establishment, from [FINDINGS.md:55-62](FINDINGS.md) and
[SETUP_COMMON.md §4](SETUP_COMMON.md):

| | Full Classical | Hybrid (PQ-enc) | Hybrid (PQ-sig) | Full PQC |
|---|---|---|---|---|
| KEM | X25519 | ML-KEM-768 | X25519 | ML-KEM-768 |
| NIST category | classical | 3 | classical | 3 |
| COSE algorithm ID | ECDH-ES+HKDF | −70768 (private use) | ECDH-ES+HKDF | −70768 |
| Device key file | `device_x25519.pem` | `device_mlkem768.pem` | `device_x25519.pem` | `device_mlkem768.pem` |
| Manifest container overhead | ~92 B | **~1144 B** | ~92 B | **~1144 B** |
| Payload container header | 74 B | **1126 B** | 74 B | **1126 B** |
| Payload AEAD tag | 16 B | 16 B | 16 B | 16 B |

Both encrypted objects carry one KEM ciphertext each, so the overhead applies
twice per update. The AEAD itself (ChaCha20-Poly1305) and its 16-byte tag are
**identical in every tier** — the entire wire cost of going post-quantum sits
in the recipient structure, not in the bulk encryption.

**Confirmed on the wire**, not just predicted: the dongle's full-PQ run
decrypted a 4892 B manifest to 3748 B of plaintext — a 1144 B delta matching
the ML-KEM-768 figure exactly — with a 1126 B payload header
([FINDINGS.md:125](FINDINGS.md)).

---

## 3. Device cost — RAM

### samr21-xpro (Cortex-M0+, 32 KB RAM) — the constrained case

Link-time `data+bss` out of **32,768 B**, ethos mode, every row rebuilt from a
clean `BINDIR` on 2026-07-23
([DEVICE_SAMR21_XPRO.md:252-273](DEVICE_SAMR21_XPRO.md#combination-matrix)).

| Tier | RAM used | Spare | Verdict |
|---|---|---|---|
| **Full Classical** | 21,552 B | 11.2 KB | ⚠️ links (row 2, the manifest-only variant, is ✅ **verified on hardware**) |
| **Hybrid (PQ-enc)** | 30,736 B | **2,032 B** | ⚠️ links — the recommended PQ demo for this board |
| **Hybrid (PQ-sig)** | 31,280 B | **1,488 B** | ⚠️ links (row 8, signature-only, is ✅ **verified 2026-07-18**) |
| **Full PQC** | **overflow 6,324 B** | — | ❌ **confirmed infeasible** |

Full PQC with the *weaker* signature does not rescue it either: ML-DSA-44 +
ML-KEM-768 still overflows by **3,556 B** (row 14). The gap is kilobytes, not
bytes — **this is not a tuning problem.**

### nRF52840 Dongle (Cortex-M4, 256 KB RAM) — the roomy case

Same four tiers, same firmware, out of **262,144 B**
([DEVICE_NRF52840_DONGLE.md:372-391](DEVICE_NRF52840_DONGLE.md#combination-matrix)):

| Tier | RAM used | % of budget | Verdict |
|---|---|---|---|
| Full Classical | 25,380 B | 9.7 % | ✅ **verified 2026-07-21** |
| Hybrid (PQ-enc) | 34,564 B | 13.2 % | 🔨 builds |
| Hybrid (PQ-sig) | 35,108 B | 13.4 % | 🔨 builds |
| **Full PQC** | 42,916 B | **16.4 %** | ✅ **FULL OTA VERIFIED 2026-07-22** |

That contrast is the headline result of the project: **the tier that is
physically impossible on one board consumes one sixth of the other's budget.**
Post-quantum SUIT updates are not blocked by the standards, the tooling, or
the protocol — only by RAM. Even the maximum-strength corner (ML-DSA-87 +
ML-KEM-1024, 47,236 B) uses 18 %.

### Why it fails — the number that is not in the linker output

The static footprint above is *not* what kills Full PQC on 32 KB. wolfCrypt's
ML-KEM decapsulation chain needs **~8.5–9 KB of working stack** at runtime
(`mlkemkey_encapsulate` alone measures 4,664 B via `-fstack-usage`), which is
why the app `Makefile` forces `SUIT_WORKER_STACKSIZE=9216` for any `ml-kem-%`
build ([FINDINGS.md:64-67](FINDINGS.md)).

Worse, those buffers were originally **heap**-allocated — up to 6,144 B in a
single `XMALLOC`, invisible to `arm-none-eabi-size` and `nm`. Every ML-KEM RAM
figure published before 2026-07-20 was link-time-only and wrong; the symptom on
hardware was `CEK derivation failed: -125` despite the linker reporting spare
RAM. See [FINDINGS.md §4](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery).
**Treat any link-time feasibility claim about a PQ tier as unproven until it
has run on the board.**

---

## 4. Device cost — ROM, and the constraint flip

RAM is only the binding limit while the board is tethered. In 802.15.4 radio
mode (`USE_ETHOS=0`) the samr21 drops `stdio_ethos` for the 6LoWPAN stack:
**~1.6 KB less RAM, but ~12 KB more `.text`** — and a riotboot slot is only
128,768 B. The binding constraint **flips from RAM to ROM**
([DEVICE_802154_PI.md:699-720](DEVICE_802154_PI.md#combination-matrix--radio-mode)).

| Tier | RAM (of 32,768 B) | ROM (of 128,768 B) | Verdict |
|---|---|---|---|
| Full Classical | 19,984 B | 116,496 B | 🔨 builds — the recommended mesh default |
| **Hybrid (PQ-enc)** | 29,168 B | 127,964 B | 🔨 builds — **804 B ROM spare** |
| **Hybrid (PQ-sig)** | — | **overflow 1,344 B** | ❌ **fits over ethos, fails on radio** |
| Full PQC | overflow 4,756 B RAM | overflow 5,984 B ROM | ❌ infeasible in both modes |

Two tier-level conclusions:

1. **Tier feasibility is a property of tier × board × network mode, never of
   the tier alone.** Hybrid (PQ-sig) is feasible tethered and infeasible
   wirelessly, on the same board, with the same crypto. Three combinations
   flip this way (samr21 rows 8a, 9, 11).
2. **The two hybrids fail differently.** Hybrid (PQ-enc) is RAM-bound and
   survives on radio; Hybrid (PQ-sig) is ROM-bound because ML-DSA verification
   code is large, and dies there. Which hybrid to pick depends on which
   resource your build is short of.

The dongle sees none of this — its slot is 448 KB and the full-PQ radio build
uses 133,656 B ([DEVICE_802154_PI.md:735-740](DEVICE_802154_PI.md)).

> **Gap:** ROM was only ever captured for **radio mode**. There are no
> ethos-mode ROM figures for any tier.

---

## 5. Security rationale — what each tier actually buys

A quantum adversary running Shor's algorithm breaks Ed25519 and X25519
outright. But the two axes have **different time horizons**, and that asymmetry
is what makes the hybrid tiers defensible rather than merely cheap:

- **Signature forgery is a *future* threat.** An attacker needs a quantum
  computer *at the moment they push malicious firmware*. A device retired
  before cryptographically-relevant quantum computers exist was never at risk
  from a classical signature.
- **Decryption is a *retroactive* threat.** An attacker can record an encrypted
  firmware image off the air **today** and decrypt it years later, once the
  hardware exists — the "harvest now, decrypt later" problem. Confidentiality
  must hold for the entire secrecy lifetime of the firmware, which for embedded
  IP is often "forever".

Consequences per tier:

| Tier | Protects against | Remains exposed to |
|---|---|---|
| Full Classical | Any classical attacker | Both quantum threats |
| **Hybrid (PQ-enc)** | Harvest-now-decrypt-later | Future forgery of new manifests |
| **Hybrid (PQ-sig)** | Future forgery | Firmware captured today, decrypted later |
| Full PQC | Both | — (within cat-2/3 margins) |

On a board where only one axis can be post-quantum, **Hybrid (PQ-enc) is the
stronger choice on threat-model grounds**: it closes the retroactive exposure,
which cannot be fixed later, and leaves open only the forward-looking one,
which a future firmware update can still migrate. Hybrid (PQ-sig) makes the
opposite bet, and any traffic recorded in the meantime is permanently
compromised. Notably, this is also the combination the samr21 handles better
(§4).

Category note: suffixes are **security categories, not versions** — ML-DSA-44
= cat 2, ML-DSA-65 = cat 3, ML-DSA-87 = cat 5; ML-KEM-768 = cat 3, ML-KEM-1024
= cat 5. The Full PQC tier as defined here is cat 3 on both axes.

---

## 6. Operational deltas — what changes on the host

| | Full Classical | Hybrid (PQ-enc) | Hybrid (PQ-sig) | Full PQC |
|---|---|---|---|---|
| OpenSSL 3.5+ required | no | **yes** | **yes** | **yes** |
| Local wolfSSL checkout (~1.1 GB copy per clean build) | no | **yes** | **yes** | **yes** |
| `SUIT_WORKER_STACKSIZE` | default | **9216 (forced)** | 4096 | **9216 (forced)** |
| Signing key generation | `gen_key.py` | `gen_key.py` | `openssl genpkey -algorithm ml-dsa-44 -provparam ml-dsa.output_formats=seed-only` | same, `ml-dsa-65` |
| Device key | `device_x25519.pem` | `device_mlkem768.pem` | `device_x25519.pem` | `device_mlkem768.pem` |
| Manifest encryption tool | `manifest-encryption/encrypt_manifest.py` | `manifest-encryption-mlkem/encrypt_manifest.py` | `manifest-encryption/…` | `manifest-encryption-mlkem/…` |
| Publish/notify cycle | **identical in all four tiers** | ← | ← | ← |

The four-step cycle (fileserver → `suit/publish` → optional manual manifest
encryption → `suit/notify`) is **the same in every tier**; only the flags and
key files change. That is the practical payoff of the design: migrating a
deployment between tiers is a rebuild, not a workflow change.

Tier-specific failure signatures ([SETUP_COMMON.md:332-337](SETUP_COMMON.md)):

| Code | Meaning | Which tier hits it |
|---|---|---|
| `res=-125` | wolfCrypt `MEMORY_E` | **PQ-enc tiers only** — ML-KEM working memory |
| `res=-213` | AEAD tag mismatch | any encrypted tier — wrong device key or algo |
| `res=-4` | manifest condition failed | any — class-ID mismatch |
| `res=-7` | payload digest mismatch | any encrypted-payload tier — storage write path |
| `res=-5` | seqnr replay | any — **expected**, publish a newer `APP_VER` |

Remember the **matching rule**: the build flags decide what the *device*
accepts, the publish flags decide what is *produced*, and the two must agree.
Plaintext always passes through regardless of tier.

---

## 7. Which tier should you use?

| Situation | Tier | Why |
|---|---|---|
| 32 KB board, tethered (ethos) | **Hybrid (PQ-enc)** | 2,032 B spare; closes the retroactive threat (§5) |
| 32 KB board, 802.15.4 mesh | **Hybrid (PQ-enc)** | the only PQ tier that fits ROM there — 804 B spare |
| 32 KB board, PQ *signature* required | **Hybrid (PQ-sig)**, ethos only | verified on hardware as signature-only; fails ROM on radio |
| 64 KB+ board | **Full PQC** | verified end to end; 16 % of the dongle's RAM |
| Teaching / CI / reference runs | **Full Classical** | fastest, no OpenSSL 3.5+ or 1.1 GB wolfSSL copy |
| Maximum strength demonstration | ML-DSA-87 + ML-KEM-1024 | dongle only, 47,236 B, builds but never run |

The single-sentence version: **on 32 KB you can have a post-quantum signature
or post-quantum encryption, never both; above that, take both.**

---

## 8. What this comparison does *not* cover

Stated explicitly so the tables are not over-read:

- **The tables above compare space, not time**, and equal feasibility does not
  imply equal speed. Time, throughput and *runtime* memory are measured by a
  separate, opt-in mechanism — see **[PERFORMANCE.md](PERFORMANCE.md)** for the
  `SUIT_PERF=1` checkpoints, the per-tier capture procedure, and how to read
  the two PQ axes apart. Captured figures land in
  [FINDINGS.md](FINDINGS.md); until a tier has been captured on a given board,
  the only performance statement on record for it remains the qualitative one —
  ML-DSA-65 verification is a "noticeable pause" on a 48 MHz M0+ versus «1 s
  for Ed25519 ([FINDINGS.md:48](FINDINGS.md)).
  Note also that instrumented builds carry `ztimer_usec` + `malloc_monitor` and
  so have a **different footprint from every figure in this document**.
- **ROM figures exist only for radio mode** (§4); ethos-mode ROM was never
  captured for any tier.
- **⚠️ "links" is not ✅ "verified".** Of the four headline tiers, only Full
  Classical and Full PQC have been run end to end on real hardware (dongle),
  plus the signature-only and manifest-only variants on the samr21. The
  ✅/⚠️/🔨/❌ distinction is preserved from the source matrices deliberately.
- **No composite hybrids** (§1) — the tiers mix axes, they do not stack two
  signatures.
- **Prototype key storage.** Every tier embeds the device's *private*
  decryption key in the firmware image, and RIOT's SUIT implementation is not
  security-audited. The tier comparison is about crypto cost, not deployment
  readiness.

---

## Appendix — all 20 combinations, grouped by tier

samr21-xpro ethos RAM / nRF52840 dongle RAM. Sources:
[DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md#combination-matrix) ·
[DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md#combination-matrix).
Row numbers are the samr21 matrix's.

**Full Classical** — Ed25519 + X25519

| # | Manifest enc | Payload enc | samr21 | dongle |
|---|---|---|---|---|
| 1 | — | — | 20,992 B ✅ | 24,820 B |
| 2 | X25519 | — | 21,160 B ✅ | 24,988 B |
| 3 | — | X25519 | 21,424 B ⚠️ | 25,252 B ✅ |
| 4 | X25519 | X25519 | 21,552 B ⚠️ | 25,380 B ✅ |

**Hybrid (PQ-enc)** — Ed25519 + ML-KEM

| # | Manifest enc | Payload enc | samr21 | dongle |
|---|---|---|---|---|
| 5 | ML-KEM-768 | — | 29,328 B ⚠️ | 33,156 B |
| 6 | ML-KEM-768 | ML-KEM-768 | 30,736 B ⚠️ (2,032 B spare) | 34,564 B |
| 7 | ML-KEM-1024 | ML-KEM-1024 | 32,720 B ⚠️ (**48 B spare**) | 36,548 B |

**Hybrid (PQ-sig)** — ML-DSA + X25519

| # | Signature | Manifest enc | Payload enc | samr21 | dongle |
|---|---|---|---|---|---|
| 8 | ML-DSA-44 | — | — | 30,760 B ✅ | 34,588 B |
| 8a | ML-DSA-44 | X25519 | — | 30,888 B ⚠️ | 34,716 B |
| 9 | ML-DSA-44 | X25519 | X25519 | 31,280 B ⚠️ | 35,108 B |
| 10 | ML-DSA-65 | — | — | 32,552 B ✅ (216 B spare) | 36,380 B |
| 11 | ML-DSA-65 | X25519 | — | 32,680 B ⚠️ (**88 B spare**) | 36,508 B |
| 12 | ML-DSA-65 | X25519 | X25519 | **overflow 308 B** ❌ | 36,900 B |
| 17 | ML-DSA-87 | — | — | **overflow 3,628 B** ❌ | 40,220 B |
| 18 | ML-DSA-87 | X25519 | — | **overflow 3,756 B** ❌ | 40,348 B |
| 19 | ML-DSA-87 | X25519 | X25519 | **overflow 4,148 B** ❌ | 40,740 B |

**Full PQC** — ML-DSA + ML-KEM

| # | Signature | Manifest enc | Payload enc | samr21 | dongle |
|---|---|---|---|---|---|
| 13 | ML-DSA-44 | ML-KEM-768 | — | **overflow 3,364 B** ❌ | 39,956 B |
| 14 | ML-DSA-44 | ML-KEM-768 | ML-KEM-768 | **overflow 3,556 B** ❌ | 40,148 B |
| 15 | ML-DSA-65 | ML-KEM-768 | ML-KEM-768 | **overflow 6,324 B** ❌ | 42,916 B ✅ |
| 16 | ML-DSA-87 | ML-KEM-1024 | ML-KEM-1024 | **overflow 10,644 B** ❌ | 47,236 B |

**Every Full PQC row fails on the samr21. Every one of them fits on the
dongle.** That is the comparison in two sentences.

---

*Sources: [FINDINGS.md](FINDINGS.md) (measurements, feasibility) ·
[GUIDE.md](GUIDE.md) (the three axes) ·
[SETUP_COMMON.md](SETUP_COMMON.md) (keys, sizes, error codes) · the four
`DEVICE_*.md` matrices. The pre-2026-07-20 matrices in
`MLKEM_ENCRYPTION_PLAN.md` / `MLKEM_ENCRYPTION_CHANGES.md` are superseded and
are deliberately not cited here.*
