# Perf results — host side (signing & encryption)

Measured timing of the **producer** half of a SUIT update: the build host that
hashes, **signs**, **encapsulates** and **encrypts**. The consumer half —
verification, decapsulation, decryption on the device — is
[PERF_RESULTS_NRF52840.md](PERF_RESULTS_NRF52840.md); every number below pairs
with one from there.

- **What is measured and how**: [PERFORMANCE.md §8](PERFORMANCE.md)
- **What the tiers are**: [CRYPTO_TIERS.md](CRYPTO_TIERS.md)
- **What each operation does**: [CRYPTO_OPERATIONS.md](CRYPTO_OPERATIONS.md)

Host: 13th Gen Intel Core i5-13600K (20 threads), WSL2
(`6.6.87.2-microsoft-standard-WSL2`), Python 3.10.12, `cryptography` 48.0.0
(bundled OpenSSL 4.0.0). Payloads and manifests produced for
`BOARD=nrf52840dongle` with `SUIT_PERF=1 PROGRESS_BAR=0`, so the artifacts are
the same ones the device runs measured.

| Tier | Signature | KEM | Status |
|---|---|---|---|
| **T1 Full Classical** | Ed25519 | X25519 | ✅ captured 2026-08-09 |
| **T2 Hybrid (PQ-enc)** | Ed25519 | ML-KEM-768 | ✅ captured 2026-08-09 |
| **T3 Hybrid (PQ-sig)** | ML-DSA-44 | X25519 | ✅ captured 2026-08-09 |
| **T4 Full PQC** | ML-DSA-65 | ML-KEM-768 | ✅ captured 2026-08-09 |
| **T5 Classical (ECDSA)** | ES256 | X25519 | ✅ captured 2026-08-09 |

**Two measurement layers**, because one sample per phase is defensible on a
bare-metal Cortex-M4 and is not defensible here ([§4](#4-methodology-validation)):

- **A — pipeline.** Five real `make suit/publish` runs with `SUIT_HOST_PERF=1`.
  One sample per phase, but it is the *true* end-to-end producer cost, process
  startup and file I/O included.
- **B — harness.** `dist/tools/suit/host_crypto_bench.py`: N = 200 (asymmetric)
  / 50 (bulk) repeats after 10 warmups, over **fixed reference bytes identical
  across all five tiers**, plus a 2,000-sample signing-latency distribution.
  **Layer B is the authoritative per-algorithm layer**; layer A carries a
  cold-start artifact that layer B removes ([§3.5](#35-the-first-asymmetric-operation-in-a-python-process-costs-20-ms)).

> ### Headline
>
> **The signature ranking inverts end-for-end.** On the device, ML-DSA
> verification is the *cheapest* signature and Ed25519 the dearest. On the
> host, ML-DSA signing is the *dearest* and Ed25519 is among the cheapest:
>
> | | ES256 | Ed25519 | ML-DSA-44 | ML-DSA-65 |
> |---|---:|---:|---:|---:|
> | **Host `sig_sign`** (median) | **16.1 µs** | **19.4 µs** | 237.9 µs | 411.6 µs |
> | **Device `sig_verify`** | 450.8 ms | 2,204.8 ms | **42.1 ms** | **72.5 ms** |
>
> The same inversion holds at tier level. Ordering the five tiers by their
> asymmetric-crypto cost gives **T2 < T5 < T1 < T3 < T4** on the host and
> **T4 < T2 < T3 < T5 < T1** on the device: *the full post-quantum tier is the
> device's cheapest and the host's dearest.*
>
> **This is the right trade, by four orders of magnitude.** Moving the
> signature axis from Ed25519 to ML-DSA-44 (T1→T3, holding the KEM fixed) costs
> **+269.9 µs once on a desktop** and saves **−2,100.4 ms on every device
> update** — a leverage of **7,782×**. For the full tier move T1→T4 it is
> **16,472×** ([§3.2](#32-the-producer-pays-microseconds-so-the-consumer-can-save-seconds)).
>
> Two facts no device measurement could produce:
>
> - **ML-DSA signing is a distribution, not a number.** Its rejection-sampling
>   loop makes ML-DSA-44 span **120.8 µs to 2,374.4 µs — 19.7× — over 2,000
>   signatures of identical input**, while Ed25519's p95 sits 6 % above its
>   median. Verification has no such spread; the randomness lives entirely on
>   the producer side ([§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number)).
> - **The producer half is free.** 147–544 µs of cryptography per publish
>   against a 9.1–12.9 s update: **0.0013 %–0.0060 %**. No producer-side
>   argument against post-quantum SUIT survives this table
>   ([§3.1](#31-producer-cryptography-is-at-most-0006--of-an-update)).

---

## 1. Cross-tier comparison

Layer B medians in µs unless stated. Bold rows are tier-sensitive; the
*invariant* block is the control.

| Indicator | T1 Classical | **T2 PQ-enc** | **T3 PQ-sig** | **T4 Full PQC** | **T5 ECDSA** |
|---|---:|---:|---:|---:|---:|
| Signature | Ed25519 | Ed25519 | ML-DSA-44 | ML-DSA-65 | ES256 |
| KEM | X25519 | ML-KEM-768 | X25519 | ML-KEM-768 | X25519 |
| **`sig_sign`** | 19.85 | 19.78 | **289.74** | **414.45** | **16.75** |
| **`mfst_kem`** (encapsulate) | 48.70 | **17.54** | 48.52 | **18.10** | 48.76 |
| `payload_kem` × 2 slots | 97.41 | **35.07** | 97.04 | **36.20** | 97.51 |
| `key_load` | 22.91 | 22.79 | **60.89** | **98.33** | 17.92 |
| Asymmetric crypto, total | 165.96 | **72.38** | 435.30 | **468.75** | 163.02 |
| **Producer crypto, total** | 240.9 | **147.2** | 510.6 | **543.6** | 237.8 |
| — as a share of the device `total` | 0.00186 % | 0.00132 % | 0.00441 % | **0.00598 %** | 0.00198 % |
| Pipeline wall time (layer A) | 26.7 ms | 22.2 ms | 35.9 ms | 25.9 ms | 27.8 ms |
| Signature on the wire | 64 B | 64 B | **2,420 B** | **3,309 B** | 64 B |
| KEM ciphertext / ephemeral key | 32 B | **1,088 B** | 32 B | **1,088 B** | 32 B |
| Manifest on the wire | 591 B | 1,643 B | 2,951 B | **4,892 B** | 591 B |
| Manifest plaintext | 499 B | **499 B** | 2,859 B | 3,748 B | **499 B** |
| COSE recipient overhead | 92 B | **1,144 B** | 92 B | **1,144 B** | 92 B |
| Payload header | 74 B | **1,126 B** | 74 B | **1,126 B** | 74 B |
| *Inv.* `mfst_digest` (499 B) | 0.974 | 0.995 | 0.984 | 0.990 | 0.977 |
| *Inv.* `mfst_aead` (499 B) | 1.274 | 1.261 | 1.264 | 1.262 | 1.258 |
| *Inv.* `payload_aead` (109,388 B) | 36.372 | 36.296 | 36.520 | 36.303 | 36.291 |
| *Inv.* `payload_aead` throughput | 3.007 GB/s | 3.014 | 2.995 | 3.013 | 3.014 |

The three controlled pairs of
[PERF_RESULTS_NRF52840.md §1](PERF_RESULTS_NRF52840.md) hold here too, and one
is stronger: **COSE signs the SHA-256 digest, not the manifest**, so
`sig_sign`'s input is the same 55 B `Sig_structure` in every tier. The
signature comparison is over byte-identical input *by construction*, not by
arrangement.

### Producer vs consumer, phase by phase

Device cost divided by host cost for the same phase. This is not a CPU
benchmark — the two ends run different operations — it is the **operational
asymmetry** of each primitive.

| Phase pair | T1 Ed25519 | T2 Ed25519 | T3 ML-DSA-44 | T4 ML-DSA-65 | T5 ES256 |
|---|---:|---:|---:|---:|---:|
| `sig_verify` ÷ `sig_sign` | **107,964×** | **114,622×** | **146×** | **175×** | **26,913×** |
| `mfst_kem` (decaps ÷ encaps) | 18,124× | **2,707×** | 18,180× | **2,659×** | 18,091× |
| `payload_aead` (109 kB) | 5,538× | 5,842× | 5,848× | 6,138× | 5,507× |
| `mfst_aead` (499 B) | 341× | 348× | 1,644× | 2,277× | 343× |
| `mfst_digest` (380–499 B) | 660× | 649× | 655× | 652× | 658× |

**The bulk-symmetric row is the yardstick: ~5,500–6,100× is simply what this
desktop is worth against a 64 MHz Cortex-M4** on the same ChaCha20-Poly1305.
Read every other row against it:

- **ML-DSA's 146–175× is far *below* the platform baseline** — the lattice
  signature is the only primitive where the constrained end does *less*
  relative work than the platform gap predicts. Its cost sits with the
  producer, which is the end that can afford it.
- **Ed25519's 108,000× is ~20× *above* it**, and ES256's 27,000× ~5× above.
  Curve signatures load the constrained end far more heavily than raw CPU
  speed accounts for.
- **ML-KEM's 2,700× is below the baseline; X25519's 18,100× is 3× above it.**
  Same conclusion on the key-establishment axis.

---

## 2. Per-tier detail

Layer A: one real `make suit/publish`, five processes, `SUIT_HOST_PERF=1`.
`total` is per process, measured from module import to exit, so it includes
argument parsing, file I/O and the object model — the residual column is that
overhead.

### 2.1 T1 — Full Classical (Ed25519 + X25519)

Manifest 499 B → 591 B on the wire; payload 109,496 B → 109,586 B published.

| Process | Phase | Time | Bytes |
|---|---|---:|---:|
| `suit-tool create` | `mfst_create` | 2.721 ms | 385 |
| `suit-tool sign` | `key_load` | 1.651 ms | 119 |
| | `mfst_digest` | 9 µs | 380 |
| | **`sig_sign`** | **97 µs** | 55 |
| | `mfst_serialize` | 5 µs | 499 |
| `encrypt_firmware` (slot 0) | **`payload_kem`** | **2.006 ms** | — |
| | `payload_aead` | 120 µs | 109,496 |
| `encrypt_firmware` (slot 1) | **`payload_kem`** | **2.148 ms** | — |
| | `payload_aead` | 122 µs | 109,496 |
| `encrypt_manifest` | **`mfst_kem`** | **1.911 ms** | — |
| | `mfst_aead` | 62 µs | 499 |
| | `mfst_cose` | — | 92 |
| **Pipeline** | | **26.701 ms** | |

Crypto 10.852 ms (40.6 %), residual 15.849 ms. **The three `*_kem` figures are
cold-start-dominated** and are 40× the steady-state 48.7 µs
([§3.5](#35-the-first-asymmetric-operation-in-a-python-process-costs-20-ms)).

### 2.2 T2 — Hybrid, PQ encryption (Ed25519 + ML-KEM-768)

Manifest 499 B → 1,643 B; payload 120,812 B, header 1,126 B.

| Process | Phase | Time | Bytes |
|---|---|---:|---:|
| `suit-tool create` | `mfst_create` | 3.183 ms | 385 |
| `suit-tool sign` | `key_load` | 1.593 ms | 119 |
| | **`sig_sign`** | **113 µs** | 55 |
| `encrypt_firmware` ×2 | **`payload_kem`** | **71 / 69 µs** | — |
| | `payload_aead` | 146 / 128 µs | 120,812 |
| `encrypt_manifest` | **`mfst_kem`** | **70 µs** | — |
| | `mfst_aead` | 65 µs | 499 |
| **Pipeline** | | **22.245 ms** | |

Crypto 5.452 ms (24.5 %). **T2 is the cheapest tier for the producer** and the
only one whose KEM phases are near steady state in layer A: ML-KEM does not
trigger the 2 ms initialisation that X25519 does.

### 2.3 T3 — Hybrid, PQ signature (ML-DSA-44 + X25519)

Manifest 2,859 B → 2,951 B; payload 121,424 B. Signature 2,420 B.

| Process | Phase | Time | Bytes |
|---|---|---:|---:|
| `suit-tool create` | `mfst_create` | 5.676 ms | 385 |
| `suit-tool sign` | `key_load` | 3.111 ms | 128 |
| | **`sig_sign`** | **1.533 ms** | 56 |
| | `mfst_serialize` | 5 µs | 2,859 |
| `encrypt_firmware` ×2 | `payload_kem` | 2.465 / 2.190 ms | — |
| | `payload_aead` | 186 / 141 µs | 121,424 |
| `encrypt_manifest` | `mfst_kem` | 2.521 ms | — |
| | `mfst_aead` | 110 µs | 2,859 |
| **Pipeline** | | **35.876 ms** | |

Crypto 17.949 ms (50.0 %) — **the most expensive producer pipeline of the
five**. The 1.533 ms `sig_sign` is a single draw from a distribution whose
median is 237.9 µs and whose maximum over 2,000 samples is 2,374 µs
([§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number)): this run
happened to land in the tail, which is exactly the point.

### 2.4 T4 — Full PQC (ML-DSA-65 + ML-KEM-768)

Manifest 3,748 B → 4,892 B; payload 126,104 B, header 1,126 B. Signature
3,309 B.

| Process | Phase | Time | Bytes |
|---|---|---:|---:|
| `suit-tool create` | `mfst_create` | 2.723 ms | 385 |
| `suit-tool sign` | `key_load` | 2.158 ms | 128 |
| | **`sig_sign`** | **815 µs** | 56 |
| | `mfst_serialize` | 6 µs | 3,748 |
| `encrypt_firmware` ×2 | **`payload_kem`** | **120 / 79 µs** | — |
| | `payload_aead` | 171 / 154 µs | 126,104 |
| `encrypt_manifest` | **`mfst_kem`** | **103 µs** | — |
| | `mfst_aead` | 77 µs | 3,748 |
| **Pipeline** | | **25.875 ms** | |

Crypto 6.416 ms (24.8 %). **The tier the device found cheapest is the one the
host finds dearest** — 543.6 µs of steady-state crypto against T2's 147.2 µs —
and it is still 25.9 ms of wall time, indistinguishable from T1's 26.7 ms in
practice.

### 2.5 T5 — Classical with ECDSA (ES256 + X25519)

Manifest 499 B → 591 B, byte-identical in size to T1's; payload 113,900 B.

| Process | Phase | Time | Bytes |
|---|---|---:|---:|
| `suit-tool create` | `mfst_create` | 2.527 ms | 385 |
| `suit-tool sign` | `key_load` | 1.960 ms | 241 |
| | **`sig_sign`** | **608 µs** | 55 |
| `encrypt_firmware` ×2 | `payload_kem` | 2.067 / 2.052 ms | — |
| | `payload_aead` | 129 / 132 µs | 113,900 |
| `encrypt_manifest` | `mfst_kem` | 1.973 ms | — |
| | `mfst_aead` | 114 µs | 499 |
| **Pipeline** | | **27.778 ms** | |

Crypto 11.581 ms (41.7 %). **ES256 is the fastest signature to produce of all
five** (16.75 µs steady state) *and* 4.9× faster to verify than Ed25519 on the
device — it is the best classical option at both ends, and is equally broken by
Shor's algorithm.

---

## 3. Findings

### 3.1 Producer cryptography is at most 0.006 % of an update

| Tier | Producer crypto (host) | Device `total` | Share |
|---|---:|---:|---:|
| T1 Classical | 240.9 µs | 13.037 s | 0.00185 % |
| T2 PQ-enc | **147.2 µs** | 11.153 s | **0.00132 %** |
| T3 PQ-sig | 510.6 µs | 11.834 s | 0.00431 % |
| T4 Full PQC | **543.6 µs** | 9.304 s | **0.00584 %** |
| T5 ECDSA | 237.8 µs | 11.700 s | 0.00203 % |

Even the worst tier's producer cryptography is **1/16,700th** of the update it
produces. The full publish *pipeline* is 22–36 ms, of which 24–50 % is
cryptography and the rest is Python process overhead — and all of it is
dominated by the firmware build that precedes it (tens of seconds).

**Consequence for the thesis: there is no producer-side cost argument against
post-quantum SUIT.** Every real objection in
[CRYPTO_TIERS.md](CRYPTO_TIERS.md) — RAM, ROM, wire size, device latency —
lives on the device. The build host does not notice which tier it is producing.

This also holds for the algorithms *no board can run*: ML-DSA-87 signs in
486.6 µs median ([§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number)).
Its infeasibility on both boards
([DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md) rows 17–19) is entirely a
device-memory result, with nothing on the producer side to add.

### 3.2 The producer pays microseconds so the consumer can save seconds

Each hybrid tier moves exactly one axis, so the trade is measurable on both
sides without modelling:

| Move | Axis | Host Δ | Device Δ | Leverage |
|---|---|---:|---:|---:|
| T1 → T3 | signature, Ed25519 → ML-DSA-44 | **+269.9 µs** | **−2,100.4 ms** | **7,782×** |
| T1 → T2 | KEM, X25519 → ML-KEM-768 | **−93.6 µs** | **−2,918.0 ms** | *both cheaper* |
| T1 → T4 | both | **+302.8 µs** | **−4,987.5 ms** | **16,472×** |

- **The signature axis is a genuine trade, and a wildly favourable one.**
  269.9 µs of extra desktop time, paid once per release, buys 2.1 s off every
  device update, forever, on every device in the fleet.
- **The key-establishment axis is not a trade at all.** ML-KEM-768 is cheaper
  than X25519 *at both ends* — 2.78× on the host, 18.6× on the device. Its
  entire cost is the 1,052 B it adds per encrypted object, twice per update,
  and the 6 KB of device stack
  ([PERF_RESULTS_NRF52840.md §3.4](PERF_RESULTS_NRF52840.md)).
- **T1 → T4 combines them**, and the leverage is the largest of the three.

> The 269.9 µs figure uses layer B medians. Taking the ML-DSA-44 **p95**
> (790.1 µs) instead gives +770.3 µs and a leverage of 2,727× — still three
> orders of magnitude. The conclusion is not sensitive to which point of the
> distribution is chosen.

### 3.3 ML-DSA signing latency is a distribution, not a number

2,000 signatures per algorithm over **identical 55 B input**, after 50 warmups
(µs):

| | min | p10 | p25 | **median** | p75 | p90 | p95 | p99 | max | mean | stdev |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ES256** | 15.8 | 15.9 | 16.0 | **16.1** | 16.2 | 16.3 | 16.8 | 21.6 | 61.2 | 16.3 | 1.6 |
| **Ed25519** | 19.0 | 19.2 | 19.3 | **19.4** | 19.6 | 19.8 | 20.6 | 25.2 | 61.2 | 19.7 | 1.8 |
| **ML-DSA-44** | 120.8 | 123.5 | 178.0 | **237.9** | 402.3 | 624.9 | 790.1 | 1,076.6 | 2,374.4 | 317.1 | 227.0 |
| **ML-DSA-65** | 176.2 | 180.4 | 257.2 | **411.6** | 645.6 | 988.7 | 1,264.3 | 1,800.5 | 3,307.7 | 509.9 | 360.3 |
| **ML-DSA-87** | 259.5 | 264.7 | 368.0 | **486.6** | 757.8 | 1,144.2 | 1,403.2 | 2,256.1 | 5,798.9 | 616.9 | 430.1 |

| | p95 / median | max / min | stdev / median |
|---|---:|---:|---:|
| ES256 | 1.04× | 3.9× | 10 % |
| Ed25519 | 1.06× | 3.2× | 9 % |
| **ML-DSA-44** | **3.32×** | **19.7×** | **95 %** |
| **ML-DSA-65** | **3.07×** | **18.8×** | **88 %** |
| **ML-DSA-87** | **2.88×** | **22.3×** | **88 %** |

**This is FIPS 204's rejection-sampling loop, visible in the data.** ML-DSA
signing draws a candidate, checks norm bounds, and restarts on failure; the
number of attempts is data-dependent. Two signatures over the same message
therefore take different times, and the distribution is quantised into
near-multiples of one attempt — ML-DSA-44's median (237.9 µs) is almost exactly
twice its minimum (120.8 µs), the one-attempt case.

The curve algorithms show nothing of the kind: their 3–4× max/min is the host's
scheduler, not the algorithm, and their p99 is within 30 % of the median.

**Three consequences:**

1. **Quote ML-DSA signing as a distribution.** A single sample can be 10× off:
   T3's real pipeline run measured 1,533 µs against a 237.9 µs median
   ([§2.3](#23-t3--hybrid-pq-signature-ml-dsa-44--x25519)). The one-run-per-tier
   method that works on the device is invalid here, and layer B exists for
   exactly this reason.
2. **The variance is entirely on the producer side.** Verification is
   straight-line — no rejection, no loop — which is why the device's
   `sig_verify` reproduces so tightly. Whatever timing-side-channel or
   scheduling concern the spread raises, it belongs to the build server, not
   the constrained node.
3. **It does not threaten the §3.2 conclusion.** Even ML-DSA-87's 5,798.9 µs
   worst case is 0.06 % of a single device update.

### 3.4 Higher ML-DSA levels cost less than their key sizes suggest

| | ML-DSA-44 | ML-DSA-65 | ML-DSA-87 |
|---|---:|---:|---:|
| `sig_sign` median | 237.9 µs | 411.6 µs | 486.6 µs |
| vs level 44 | — | 1.73× | 2.05× |
| Signature size | 2,420 B | 3,309 B | 4,627 B |
| vs level 44 | — | 1.37× | 1.91× |
| `key_load` (seed expansion) | 60.9 µs | 98.3 µs | — |

Signing cost grows *slower* than linearly in the security level, and the
level 65 → 87 step (1.18×) is much cheaper than the 44 → 65 one (1.73×). The
device shows the same shape on the verification side — 42.1 → 72.5 ms, 1.72×,
for the same 44 → 65 step, **matching the host's 1.73× to within 1 %**. Signing
and verification scale together across levels even though their absolute costs
differ by 146–175×.

`key_load` is not free for ML-DSA: the PEM holds only the 32 B FIPS 204 seed,
which is expanded into the full signing key on load — 60.9 / 98.3 µs against
Ed25519's 22.9 µs and ES256's 17.9 µs. At level 65 the key load costs **24 % as
much as the signature itself**, which no other tier approaches.

### 3.5 The first asymmetric operation in a Python process costs 2.0 ms

Layer A's `mfst_kem` / `payload_kem` figures for the X25519 tiers (1.9–2.5 ms)
are 40–50× layer B's 48.7 µs. Isolated in a fresh process, in the order
`encrypt_manifest.py` executes:

| | 1st `X25519PrivateKey.generate()` | 2nd | `exchange()` | 1st HKDF | 2nd HKDF |
|---|---:|---:|---:|---:|---:|
| run 1 | **2,005.5 µs** | 23.4 | 98.7 | 103.5 | 4.5 |
| run 2 | **2,085.8 µs** | 22.8 | 80.3 | 103.8 | 4.7 |
| run 3 | **1,934.6 µs** | 22.3 | 74.9 | 109.6 | 5.2 |

**~2.0 ms of one-time OpenSSL provider initialisation, charged to whichever
primitive touches it first** — ~90× the 22 µs steady-state operation. HKDF pays
a smaller version of the same (104 µs → 4.7 µs).

It lands differently per tool, which is why layer A's phases are uneven:

- `suit-tool sign` pays it in `key_load` (1.6–3.1 ms), leaving `sig_sign`
  near steady state.
- `encrypt_manifest.py` / `encrypt_firmware.py` pay it inside `*_kem`, because
  the preceding PEM load uses a different code path that does not trigger it.
- **The ML-KEM tiers barely pay it at all** (70–120 µs), which is why T2 and T4
  look disproportionately cheap in layer A.

A publish is five separate processes, so an X25519 tier pays this ~3 times —
**~6 ms, more than half of T1's entire 10.9 ms measured pipeline
cryptography, and 25× the actual crypto**. It is a property of the Python
tooling, not of any algorithm.

**Rule: use layer B for every algorithm comparison. Layer A answers "what does
a publish cost", and its answer is mostly process startup.**

### 3.6 The symmetric and hashing controls are flat, and the host is ~5,500× the device

| Control | T1 | T2 | T3 | T4 | T5 | Spread |
|---|---:|---:|---:|---:|---:|---:|
| `mfst_digest` (499 B) | 0.974 | 0.995 | 0.984 | 0.990 | 0.977 | **2.2 %** |
| `mfst_aead` (499 B) | 1.274 | 1.261 | 1.264 | 1.262 | 1.258 | **1.3 %** |
| `payload_aead` (109,388 B) | 36.372 | 36.296 | 36.520 | 36.303 | 36.291 | **0.6 %** |

`payload_aead` runs at **2.995–3.014 GB/s**, against the device's
543.0–570.0 kB/s — a factor of **5,507–6,138×**. That is the honest platform
baseline for this pair of machines on identical work, and the number against
which every asymmetric ratio in [§1](#producer-vs-consumer-phase-by-phase)
should be read.

The bulk figure is the trustworthy one. `mfst_aead` at 499 B gives only 341×
because a 1.26 µs measurement on the host is mostly per-call overhead — the
device's 434 µs for the same 499 B is nearly all cipher. **Small-input
host/device ratios are not platform ratios.**

### 3.7 Every wire-format figure reproduces the device's, byte for byte

The producer side independently confirms the sizes
[PERF_RESULTS_NRF52840.md §3.11](PERF_RESULTS_NRF52840.md) measured on hardware:

| Figure | T1 | T2 | T3 | T4 | T5 | Device agrees |
|---|---:|---:|---:|---:|---:|:--:|
| Manifest plaintext | 499 | 499 | 2,859 | 3,748 | 499 | ✅ |
| Manifest on the wire | 591 | 1,643 | 2,951 | 4,892 | 591 | ✅ |
| COSE recipient overhead | 92 | 1,144 | 92 | 1,144 | 92 | ✅ |
| Payload header | 74 | 1,126 | 74 | 1,126 | 74 | ✅ |
| Payload installed | 109,496 | 120,812 | 121,424 | 126,104 | 113,900 | ✅ (T1: +108 B) |
| Signature | 64 | 64 | 2,420 | 3,309 | 64 | ✅ |
| KEM ct / ephemeral key | 32 | 1,088 | 32 | 1,088 | 32 | — |

Four of five payload sizes match to the byte across a six-day gap and a
different build invocation. **T1's is 108 B larger** than the 2026-08-03 run's
109,388 B; T1 is also the tier whose device-side `payload_aead` throughput was
the outlier in that capture
([PERF_RESULTS_NRF52840.md §4](PERF_RESULTS_NRF52840.md)). The 108 B is a build
difference, not a measurement one, and it affects no ratio here — all bulk
comparisons are throughputs.

---

## 4. Methodology validation

**Layer B is trustworthy for the asymmetric phases** — the controls in
[§3.6](#36-the-symmetric-and-hashing-controls-are-flat-and-the-host-is-5500-the-device)
hold to 0.6–2.2 % across five separately-loaded tiers, and the two algorithms
that appear in more than one tier repeat tightly:

| Repeat measurement | | | | Drift |
|---|---:|---:|---:|---:|
| `sig_sign`, Ed25519 (T1, T2) | 19.845 | 19.778 µs | | **0.34 %** |
| `mfst_kem`, X25519 (T1, T3, T5) | 48.703 | 48.520 | 48.755 µs | **0.48 %** |
| `mfst_kem`, ML-KEM-768 (T2, T4) | 17.537 | 18.099 µs | | **3.2 %** |
| `key_load`, Ed25519 (T1, T2) | 22.911 | 22.785 µs | | **0.55 %** |
| `payload_aead` (all five) | 36.291–36.520 µs | | | **0.6 %** |

Tighter than the device's 5.8 % CPU-bound noise floor, because these are 200
repeats in one process rather than one sample per binary.

**Three things this measurement is not:**

- **Not a library benchmark.** Every number is `cryptography` 48.0.0 on its
  bundled OpenSSL 4.0.0, on x86-64 with AVX2. A different backend would move
  all of them. What survives is the *relative* structure — the inversion in
  [§1](#1-cross-tier-comparison), the rejection-sampling spread in
  [§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number) — because
  those are properties of the algorithms.
- **Not a like-for-like platform comparison.** Host and device run *different
  operations* (sign vs verify, encapsulate vs decapsulate). The ratios in
  [§1](#producer-vs-consumer-phase-by-phase) are producer-vs-consumer cost, and
  §3.6's 5,500× symmetric baseline is provided precisely so they can be read
  against a same-operation control.
- **Not free of scope asymmetry, and it cuts against the host.** Host
  `mfst_kem` for X25519 includes *ephemeral key generation*, which the device
  never does (it holds a static key); host ML-KEM `mfst_kem` is *encapsulation
  only*, while the device's decapsulation internally re-runs encapsulation for
  the FIPS 203 implicit-rejection check and re-expands its key from a seed. So
  the host's X25519 figure is an upper bound and its ML-KEM figure a lower one
  — both biases inflate the X25519/ML-KEM ratio reported for the host, and the
  host ratio (2.78×) is nonetheless far *smaller* than the device's (18.6×).
  The conclusion is robust to the bias.

**No CPU pinning, no isolated cores, WSL2, and turbo/thermal management
active.** The 200-repeat medians absorb this; the maxima in
[§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number) partly do not
— Ed25519's 61.2 µs max is a scheduling artifact, not the algorithm. **Quote
medians and percentiles, never maxima**, except for ML-DSA where the tail *is*
the finding and its p99 (1,076–2,256 µs) is well clear of any scheduling noise.

---

## 5. Capture notes

### All five tiers

- Instrumentation is opt-in via `SUIT_HOST_PERF=1`
  ([PERFORMANCE.md §8](PERFORMANCE.md)). With it unset, `suit-tool sign`
  produces a **byte-identical** manifest and writes nothing to stderr —
  verified by signing the same input with the pre-change tools from git and
  `cmp`-ing the outputs.
- All five publishes were `make clean suit/publish` with `SUIT_PERF=1
  PROGRESS_BAR=0 BOARD=nrf52840dongle` and a fresh `APP_VER`, so the payloads
  match the device runs. **No hardware was involved** — `suit/publish` never
  touches a board.
- Each `encrypt_*.py` self-test (`Self-test decrypt: OK`) passed in every run,
  so the instrumented tools still produce containers that round-trip.
- `SUIT_HOST_PERF_LOG` appends, which is how the five processes of one publish
  land in one CSV.

### Keys

No key generation was needed: all five tiers' signing and device keys already
exist in the repo (`ed25519-keys/`, `mldsa44-keys/`, `mldsa-keys/`,
`es256-keys/`). ML-DSA-87 appears in
[§3.3](#33-ml-dsa-signing-latency-is-a-distribution-not-a-number) and
[§3.4](#34-higher-ml-dsa-levels-cost-less-than-their-key-sizes-suggest) only,
from `mldsa87-keys/`; it has no tier because **no board can verify it**.

### Two OpenSSLs

Keys are *generated* by the system `openssl` (3.5.5) via `suit/genkey`, but
*used* by `cryptography` 48.0.0's bundled OpenSSL 4.0.0. Every timing here is
the latter. This is worth knowing before attributing any figure to "OpenSSL".

### What is not measured

- **The firmware build itself**, which dominates a real release by tens of
  seconds and is unaffected by the crypto tier except through ML-DSA/ML-KEM
  pulling in the local wolfSSL checkout.
- **Key generation.** `openssl genpkey` for ML-DSA/ML-KEM is a one-off
  provisioning cost, not a per-release one.
- **`suit-tool` process startup before `hostperf` is imported** — Python
  interpreter boot and the `cryptography` import are outside every `total`.

---

## 6. Raw data

### Layer B — harness (`~/suit-perf-logs/host-bench.txt`)

```
# host_crypto_bench: repeats=200 aead_repeats=50 warmup=10
# sig_structure=55 B  manifest=499 B  payload=109388 B
HOSTBENCH,tier,sig_algo,kem_algo,phase,n,median_us,min_us,p95_us,stdev_us,bytes
HOSTBENCH,T1,ed25519,x25519,key_load,200,22.911,22.449,31.199,14.832,119
HOSTBENCH,T1,ed25519,x25519,sig_sign,200,19.845,19.559,21.334,2.150,55
HOSTBENCH,T1,ed25519,x25519,sig_bytes,1,0.000,0.000,0.000,0.000,64
HOSTBENCH,T1,ed25519,x25519,mfst_kem,200,48.703,47.929,54.921,3.426,0
HOSTBENCH,T1,ed25519,x25519,kem_ct_bytes,1,0.000,0.000,0.000,0.000,32
HOSTBENCH,T1,ed25519,x25519,mfst_digest,200,0.974,0.902,1.052,0.044,499
HOSTBENCH,T1,ed25519,x25519,mfst_aead,200,1.274,1.230,1.346,0.671,499
HOSTBENCH,T1,ed25519,x25519,payload_aead,50,36.372,36.123,38.214,1.344,109388
HOSTBENCH,T2,ed25519,ml-kem-768,key_load,200,22.785,22.492,26.513,3.017,119
HOSTBENCH,T2,ed25519,ml-kem-768,sig_sign,200,19.778,19.478,21.219,3.334,55
HOSTBENCH,T2,ed25519,ml-kem-768,sig_bytes,1,0.000,0.000,0.000,0.000,64
HOSTBENCH,T2,ed25519,ml-kem-768,mfst_kem,200,17.537,17.186,18.182,1.855,0
HOSTBENCH,T2,ed25519,ml-kem-768,kem_ct_bytes,1,0.000,0.000,0.000,0.000,1088
HOSTBENCH,T2,ed25519,ml-kem-768,mfst_digest,200,0.995,0.913,1.100,0.042,499
HOSTBENCH,T2,ed25519,ml-kem-768,mfst_aead,200,1.261,1.221,1.304,0.031,499
HOSTBENCH,T2,ed25519,ml-kem-768,payload_aead,50,36.296,36.178,38.762,6.863,109388
HOSTBENCH,T3,ml-dsa-44,x25519,key_load,200,60.892,59.874,71.155,4.476,128
HOSTBENCH,T3,ml-dsa-44,x25519,sig_sign,200,289.740,122.086,849.221,238.256,55
HOSTBENCH,T3,ml-dsa-44,x25519,sig_bytes,1,0.000,0.000,0.000,0.000,2420
HOSTBENCH,T3,ml-dsa-44,x25519,mfst_kem,200,48.520,47.845,55.599,2.726,0
HOSTBENCH,T3,ml-dsa-44,x25519,kem_ct_bytes,1,0.000,0.000,0.000,0.000,32
HOSTBENCH,T3,ml-dsa-44,x25519,mfst_digest,200,0.984,0.912,1.091,0.402,499
HOSTBENCH,T3,ml-dsa-44,x25519,mfst_aead,200,1.264,1.220,1.315,0.031,499
HOSTBENCH,T3,ml-dsa-44,x25519,payload_aead,50,36.520,36.344,38.553,1.532,109388
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,key_load,200,98.330,96.496,106.107,6.321,128
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,sig_sign,200,414.454,176.667,1204.777,375.843,55
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,sig_bytes,1,0.000,0.000,0.000,0.000,3309
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,mfst_kem,200,18.099,17.792,18.583,1.203,0
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,kem_ct_bytes,1,0.000,0.000,0.000,0.000,1088
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,mfst_digest,200,0.990,0.911,1.068,0.044,499
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,mfst_aead,200,1.262,1.222,1.310,0.031,499
HOSTBENCH,T4,ml-dsa-65,ml-kem-768,payload_aead,50,36.303,36.116,37.384,1.155,109388
HOSTBENCH,T5,es256,x25519,key_load,200,17.916,17.500,19.512,1.376,241
HOSTBENCH,T5,es256,x25519,sig_sign,200,16.750,16.428,18.591,1.744,55
HOSTBENCH,T5,es256,x25519,sig_bytes,1,0.000,0.000,0.000,0.000,64
HOSTBENCH,T5,es256,x25519,mfst_kem,200,48.755,48.149,55.927,5.471,0
HOSTBENCH,T5,es256,x25519,kem_ct_bytes,1,0.000,0.000,0.000,0.000,32
HOSTBENCH,T5,es256,x25519,mfst_digest,200,0.977,0.912,1.035,0.047,499
HOSTBENCH,T5,es256,x25519,mfst_aead,200,1.258,1.218,1.293,0.033,499
HOSTBENCH,T5,es256,x25519,payload_aead,50,36.291,36.122,42.633,1.880,109388
```

### Signing-latency distribution, N = 2,000 (`~/suit-perf-logs/host-sig-distribution.txt`)

```
sig_structure = 55 B, N = 2000
algo,n,min,p10,p25,median,p75,p90,p95,p99,max,mean,stdev,siglen
ed25519,2000,19.0,19.2,19.3,19.4,19.6,19.8,20.6,25.2,61.2,19.7,1.8,64
es256,2000,15.8,15.9,16.0,16.1,16.2,16.3,16.8,21.6,61.2,16.3,1.6,64
ml-dsa-44,2000,120.8,123.5,178.0,237.9,402.3,624.9,790.1,1076.6,2374.4,317.1,227.0,2420
ml-dsa-65,2000,176.2,180.4,257.2,411.6,645.6,988.7,1264.3,1800.5,3307.7,509.9,360.3,3309
ml-dsa-87,2000,259.5,264.7,368.0,486.6,757.8,1144.2,1403.2,2256.1,5798.9,616.9,430.1,4627
```

### Layer A — pipeline CSVs

`~/suit-perf-logs/host-T{1..5}.csv`. T1 and T4 in full; the rest follow the
same shape.

**T1 — Full Classical**

```
HOSTCFG,suit-tool-create,none,none,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,suit-tool-create,none,none,mfst_create,2721,385,1,1
HOSTPERF,suit-tool-create,none,none,total,6445,0,1,0
HOSTCFG,suit-tool-sign,ed25519,none,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,suit-tool-sign,ed25519,none,key_load,1651,119,1,1
HOSTPERF,suit-tool-sign,ed25519,none,mfst_digest,9,380,1,1
HOSTPERF,suit-tool-sign,ed25519,none,sig_sign,97,55,1,1
HOSTPERF,suit-tool-sign,ed25519,none,mfst_serialize,5,499,1,1
HOSTPERF,suit-tool-sign,ed25519,none,sig_bytes,0,64,0,1
HOSTPERF,suit-tool-sign,ed25519,none,total,5223,0,1,0
HOSTCFG,encrypt-firmware,none,x25519,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-firmware,none,x25519,payload_kem,2006,0,1,0
HOSTPERF,encrypt-firmware,none,x25519,payload_aead,120,109496,1,1
HOSTPERF,encrypt-firmware,none,x25519,payload_hdr,0,74,0,1
HOSTPERF,encrypt-firmware,none,x25519,total,5005,0,1,0
HOSTCFG,encrypt-firmware,none,x25519,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-firmware,none,x25519,payload_kem,2148,0,1,0
HOSTPERF,encrypt-firmware,none,x25519,payload_aead,122,109496,1,1
HOSTPERF,encrypt-firmware,none,x25519,payload_hdr,0,74,0,1
HOSTPERF,encrypt-firmware,none,x25519,total,5245,0,1,0
HOSTCFG,encrypt-manifest,none,x25519,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-manifest,none,x25519,mfst_kem,1911,0,1,0
HOSTPERF,encrypt-manifest,none,x25519,mfst_aead,62,499,1,1
HOSTPERF,encrypt-manifest,none,x25519,mfst_cose,0,92,0,1
HOSTPERF,encrypt-manifest,none,x25519,total,4783,0,1,0
```

**T4 — Full PQC**

```
HOSTCFG,suit-tool-create,none,none,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,suit-tool-create,none,none,mfst_create,2723,385,1,1
HOSTPERF,suit-tool-create,none,none,total,5977,0,1,0
HOSTCFG,suit-tool-sign,ml-dsa-65,none,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,suit-tool-sign,ml-dsa-65,none,key_load,2158,128,1,1
HOSTPERF,suit-tool-sign,ml-dsa-65,none,mfst_digest,10,380,1,1
HOSTPERF,suit-tool-sign,ml-dsa-65,none,sig_sign,815,56,1,1
HOSTPERF,suit-tool-sign,ml-dsa-65,none,mfst_serialize,6,3748,1,1
HOSTPERF,suit-tool-sign,ml-dsa-65,none,sig_bytes,0,3309,0,1
HOSTPERF,suit-tool-sign,ml-dsa-65,none,total,6419,0,1,0
HOSTCFG,encrypt-firmware,none,ml-kem-768,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_kem,120,0,1,0
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_aead,171,126104,1,1
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_hdr,0,1126,0,1
HOSTPERF,encrypt-firmware,none,ml-kem-768,total,4376,0,1,0
HOSTCFG,encrypt-firmware,none,ml-kem-768,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_kem,79,0,1,0
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_aead,154,126104,1,1
HOSTPERF,encrypt-firmware,none,ml-kem-768,payload_hdr,0,1126,0,1
HOSTPERF,encrypt-firmware,none,ml-kem-768,total,3693,0,1,0
HOSTCFG,encrypt-manifest,none,ml-kem-768,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,encrypt-manifest,none,ml-kem-768,mfst_kem,103,0,1,0
HOSTPERF,encrypt-manifest,none,ml-kem-768,mfst_aead,77,3748,1,1
HOSTPERF,encrypt-manifest,none,ml-kem-768,mfst_cose,0,1144,0,1
HOSTPERF,encrypt-manifest,none,ml-kem-768,total,5410,0,1,0
```

### Cold-start isolation

```
devkey_gen=  2005.5  eph_gen=   23.4  exchange=   98.7  hkdf#1=   103.5  hkdf#2=   4.5 us
devkey_gen=  2085.8  eph_gen=   22.8  exchange=   80.3  hkdf#1=   103.8  hkdf#2=   4.7 us
devkey_gen=  1934.6  eph_gen=   22.3  exchange=   74.9  hkdf#1=   109.6  hkdf#2=   5.2 us
```

---

## 7. Reproducing this

```bash
# Layer B -- authoritative per-algorithm numbers, no hardware, ~1 minute
python3 dist/tools/suit/host_crypto_bench.py --repeats 200 --aead-repeats 50

# Layer A -- one tier's real pipeline (T4 shown); repeat per tier
export SUIT_PERF=1 PROGRESS_BAR=0 BOARD=nrf52840dongle
export SUIT_HOST_PERF=1 SUIT_HOST_PERF_LOG=$HOME/suit-perf-logs/host-T4.csv
export SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys SUIT_KEY=mldsa65
APP_VER=$(date +%s) SUIT_COAP_SERVER='[2001:db8::1]' \
  make -C examples/advanced/suit_update clean suit/publish
SUIT_HOST_PERF=1 SUIT_HOST_PERF_LOG=$HOME/suit-perf-logs/host-T4.csv \
  python3 examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py \
    --key $SUIT_KEY_DIR/device_mlkem768.pem \
    -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
    coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin
```

**Always `unset SUIT_KEY_ALGO SUIT_MANIFEST_ENCRYPT_ALGO` between tiers** —
stale exports are the easiest way to mislabel a run, exactly as in
[PERF_RUNBOOK_NRF52840.md §4](PERF_RUNBOOK_NRF52840.md).

---

*Related: [PERF_RESULTS_NRF52840.md](PERF_RESULTS_NRF52840.md) (the device
half) · [PERFORMANCE.md](PERFORMANCE.md) (the instrument) ·
[CRYPTO_TIERS.md](CRYPTO_TIERS.md) (space) ·
[CRYPTO_OPERATIONS.md](CRYPTO_OPERATIONS.md) (what each operation does).*
