# Perf results — nRF52840 Dongle

Measured timing and runtime-memory results from instrumented (`SUIT_PERF=1`)
SUIT updates on real hardware. **Every figure is a median over 47–50
consecutive updates per crypto tier**, captured unattended with
`dist/tools/suit/device_perf_bench.py` on 2026-08-10. An earlier edition of
this document reported one hand-driven run per tier; §3.12–3.14 and the
corrections in §4 are what the repeats changed.

- **Capture procedure**: [PERF_RUNBOOK_NRF52840.md](PERF_RUNBOOK_NRF52840.md)
- **What each phase means**: [PERFORMANCE.md](PERFORMANCE.md)
- **What the tiers are**: [CRYPTO_TIERS.md](CRYPTO_TIERS.md)

Board: nRF52840 Dongle (Cortex-M4F @ 64 MHz, 256 KB RAM), CDC-ECM transport,
`PROGRESS_BAR=0`, 64 B CoAP blocks.

| Tier | Signature | KEM | Status |
|---|---|---|---|
| **T1 Full Classical** | Ed25519 | X25519 | ✅ captured 2026-08-03 |
| **T2 Hybrid (PQ-enc)** | Ed25519 | ML-KEM-768 | ✅ captured 2026-08-03 |
| **T3 Hybrid (PQ-sig)** | ML-DSA-44 | X25519 | ✅ captured 2026-08-03 |
| **T4 Full PQC** | ML-DSA-65 | ML-KEM-768 | ✅ captured 2026-08-03 |
| **T5 Classical (ECDSA)** | ES256 | X25519 | ✅ captured 2026-08-03 |

**All four tiers measured, plus the ES256 control.** Because the hybrids each
hold one axis fixed, every algorithm was measured at least **twice** — X25519
three times, reproducing to 0.08 % ([§4](#4-methodology-validation)).

> This document supersedes [CRYPTO_TIERS.md §8](CRYPTO_TIERS.md)'s statement
> that *"no timing, latency, throughput, or energy data exists"*.

> ### Headline
>
> **The full post-quantum tier is the fastest of the four — 9.30 s against the
> classical tier's 13.04 s, a 28.6 % reduction in update latency.** Every tier
> that replaces a classical primitive gets faster:
>
> | | T1 Classical | T2 PQ-enc | T3 PQ-sig | T4 Full PQC |
> |---|---:|---:|---:|---:|
> | `total` (median) | 13.04 s | 11.15 s | 11.83 s | **9.30 s** |
>
> All figures are medians over 47–50 consecutive updates per tier, captured
> with `device_perf_bench.py`. The earlier edition of this document reported
> one hand-driven run per tier; the ranking and every conclusion below survive
> the repeats unchanged, but two space figures did not — see the stack rows in
> §1 and the excursions in §2.4 and §2.5.
>
> On this platform both post-quantum axes are **cheaper in time and dearer in
> space**: signature verification **~30–52× faster**, key establishment
> **~18.5× faster**, paid for with 1,052 B more per encrypted object and up to
> 3,245 B more per signature.
>
> **The ES256 control (T5) splits that signature speed-up in two**, and both
> halves are real:
>
> **52.6× = 4.9× (faster library) × 10.7× (faster algorithm)**
>
> Ed25519 on c25519 costs 2,216 ms; the *same classical security level* on
> wolfCrypt (ES256) costs 451 ms; ML-DSA-44 on the same wolfCrypt costs 42 ms.
> The Ed25519 figure is the mean of the two tiers that use it, which the
> repeats show are not interchangeable — 2,143 ms in T1 against 2,290 ms in T2
> (§2.2) — so the library factor is 4.75× or 5.08× depending on the build, and
> the combined factor spans 50.8×–54.3×.
> So lattice verification is **10.7× faster than ECDSA measured in the same
> library** — the algorithmic advantage survives the control
> ([§3.1](#31-the-signature-speed-up-splits-into-49-library--107-algorithm)).
>
> Two runtime facts that static analysis could not establish are now measured
> twice each: ML-KEM decapsulation allocated **zero heap**
> ([§3.5](#35-zero-heap-with-ml-kem-running--the-wolfssl_no_malloc-fix-is-confirmed-at-runtime)),
> and peaked at **8,792 B of stack — 95.4 % of the forced 9,216 B budget, to
> the byte in both ML-KEM tiers**
> ([§3.4](#34-ml-kem-uses-8792-b-of-stack--and-8996-b-in-the-worst-case-leaving-220-b)).

---

## 1. Cross-tier comparison

Bold rows are tier-sensitive; the *invariant* block at the bottom is the
control ([§4](#4-methodology-validation)).

| Indicator | T1 Classical | **T2 PQ-enc** | **T3 PQ-sig** | **T4 Full PQC** | **T5 ECDSA** |
|---|---:|---:|---:|---:|---:|
| Signature | Ed25519 | Ed25519 | ML-DSA-44 | ML-DSA-65 | ES256 |
| KEM | X25519 | ML-KEM-768 | X25519 | ML-KEM-768 | X25519 |
| *n* (updates measured) | 50 | 50 | 47 | 49 | 48 |
| **`sig_verify`** | 2.1428 s | 2.2900 s | **42.1 ms** | **72.5 ms** | **450.8 ms** |
| **`mfst_kem`** (clean CPU) | 883.4 ms | **47.4 ms** | 882.4 ms | **48.1 ms** | 882.3 ms |
| `payload_kem` (under load — [not comparable](#36-payload_kem-is-wall-clock-under-load-and-is-not-cross-tier-comparable)) | 2.1653 s | 82.8 ms | 2.1645 s | 82.7 ms | 2.1644 s |
| Asymmetric crypto, total | 5.191 s (39.8 %) | 2.420 s (21.7 %) | 3.089 s (26.1 %) | **0.203 s (2.2 %)** | 3.498 s (29.9 %) |
| **`total`** | 13.037 s | 11.153 s | 11.834 s | **9.304 s** | 11.700 s |
| `total` − 1 s sleep | 12.037 s | 10.157 s | 10.834 s | **8.309 s** | 10.704 s |
| `total` range (min – max) | 12.85 – 13.39 s | 10.82 – 11.61 s | 11.61 – 14.77 s | 9.13 – 10.27 s | 11.48 – 13.81 s |
| Manifest on the wire | 591 B | 1,643 B | 2,951 B | **4,892 B** | **591 B** |
| Manifest plaintext | 499 B | **499 B** | 2,859 B | 3,748 B | **499 B** |
| COSE signature object | 111 B | **111 B** | 2,469 B | 3,358 B | **111 B** |
| KEM recipient overhead | 92 B | **1,144 B** | 92 B | **1,144 B** | 92 B |
| Payload header | 74 B | **1,126 B** | 74 B | **1,126 B** | 74 B |
| Payload installed | 109,496 B | 120,812 B | 121,424 B | **126,104 B** | 113,900 B |
| **Stack peak** (worst observed) | 2,964 / 6,144 B (48 %) | **8,792 / 9,216 B (95 %)** | 3,372 / 4,096 B (82 %) | **8,996 / 9,216 B (98 %)** | 3,684 / 6,144 B (60 %) |
| Stack peak (common case) | 2,756 B (49/50) | 8,792 B (50/50) | 3,372 B (47/47) | 8,792 B (47/49) | 3,480 B (25/48) |
| **Stack margin, worst case** | 3,180 B | 424 B | 724 B | **220 B** | 2,460 B |
| Stack peak phase | `payload_kem` | `payload_kem` | `payload_kem` | `payload_kem` | **`sig_verify`** |
| **Heap high-water** | 0 B | **0 B** ✅ | 0 B | **0 B** ✅ | 0 B |
| *Inv.* `payload_aead` throughput | 562.4 kB/s | 569.7 kB/s | 569.2 kB/s | 565.9 kB/s | 569.7 kB/s |
| *Inv.* `storage_write` throughput | 31.8 kB/s | 32.0 kB/s | 32.0 kB/s | 32.1 kB/s | 31.8 kB/s |
| *Inv.* `image_digest` throughput | 679.7 kB/s | 680.2 kB/s | 680.2 kB/s | 681.9 kB/s | 680.9 kB/s |
| *Inv.* `mfst_digest` (380 B) | 643 µs | 645 µs | 644 µs | 645 µs | 643 µs |
| *Inv.* `mfst_aead` (499 B) | 438 µs | 439 µs | — | — | 432 µs |
| *Inv.* `mfst_fetch` (591 B) | 15.19 ms | — | — | — | 15.03 ms |
| *Inv.* CBOR/condition walk | 88 ms | 73 ms | 88 ms | 88 ms | 89 ms |
| *Inv.* 1 s sleep artifact | 999.8 ms | 996.0 ms | 1000.0 ms | 995.0 ms | 996.0 ms |
| *Host-dep.* network throughput | 39.4 kB/s | 36.9 kB/s | 37.7 kB/s | 37.3 kB/s | 38.1 kB/s |

Three controlled pairs make the axes separable **by construction, not by
assumption**:

- **T1 vs T2** — identical 499 B manifest plaintext, identical Ed25519
  signature, identical 111 B COSE object. **Only the KEM differs.**
- **T1 vs T3** — same KEM, same encryption. **Only the signature differs.**
- **T1 vs T5** — identical manifest, identical 111 B COSE object, same KEM,
  same manifest buffer, same worker stack, **same pinned wolfSSL tree**. Only
  the signature *backend* differs: c25519 vs wolfCrypt.

---

## 2. Per-tier detail

### 2.1 T1 — Full Classical (Ed25519 + X25519)

[Dongle matrix row 4](DEVICE_NRF52840_DONGLE.md#combination-matrix--nrf52840-dongle).
Manifest 591 B → 499 B; payload 109,586 B → 109,496 B installed.

**n = 50 consecutive updates** (`device_perf_bench.py --tier T1 --repeats 50`,
2026-08-10), replacing the single hand-driven run this section previously
reported. Byte, call and chunk counts were identical in all 50 runs; only
timings vary. The payload is 108 B larger than in the original capture — the
application binary changed in the interim — so absolute transfer times are not
directly comparable with it, although every ratio below is.

| Phase | Median | Min – Max | Bytes | Calls | Chunks |
|---|---:|---:|---:|---:|---:|
| `total` | **13.037 s** | 12.850 – 13.391 s | — | 1 | — |
| `mfst_fetch` | 15.19 ms | 14.23 – 26.84 ms | 591 | 1 | 1 |
| `mfst_cose` | 0.114 ms | 0.113 – 0.114 ms | 92 | 1 | 1 |
| **`mfst_kem`** | **883.41 ms** | 883.11 – 883.82 ms | — | 1 | — |
| `mfst_aead` | 0.438 ms | 0.438 – 0.439 ms | 499 | 1 | 1 |
| `parse` *(envelope)* | 11.138 s | 10.956 – 11.497 s | — | 1 | — |
| **`sig_verify`** | **2.1428 s** | 2.1425 – 2.1433 s | 111 | 1 | 1 |
| `mfst_digest` | 0.643 ms | 0.643 – 0.643 ms | 380 | 1 | 1 |
| `payload_fetch` *(envelope)* | 8.584 s | 8.403 – 8.943 s | 109,586 | 1 | 1,713 |
| **`payload_kem`** | **2.1653 s** | 2.1649 – 2.1657 s | — | 1 | — |
| `payload_aead` | 194.70 ms | 194.59 – 195.22 ms | 109,496 | 3,424 | 3,423 |
| `storage_write` | 3.446 s | 3.404 – 3.489 s | 109,496 | 3,424 | 3,424 |
| `image_digest` | 322.18 ms | 321.87 – 322.77 ms | 218,992 | 2 | 2 |
| `hdr_validate` | 4 µs | 4 – 5 µs | — | 1 | — |

Stack peak **2,756 of 6,144 B (44.9 %)** at `payload_kem` in 49 of the 50 runs
and **2,964 B (48.2 %)** in one, always at that same phase; heap 0 B in all 50.
The single-run figure previously quoted here was the common case, not the worst
case — **budget against 2,964 B.** This is the one number a single run could not
have found, and it moves the T1 headroom claim by 208 B.

```
total (median)                            13.037 s   100.0 %
├─ mfst_fetch                              0.015 s     0.1 %
├─ manifest decrypt (cose+kem+aead)        0.884 s     6.8 %
├─ riotboot_hdr_print + ztimer_sleep(1s)   1.000 s     7.7 %   ← artifact
├─ parse                                  11.138 s    85.4 %
│  ├─ sig_verify                           2.143 s    16.4 %
│  ├─ payload_fetch                        8.584 s    65.8 %
│  │  ├─ payload_kem                       2.165 s    16.6 %
│  │  ├─ storage_write                     3.446 s    26.4 %
│  │  ├─ payload_aead                      0.195 s     1.5 %
│  │  └─ network / CoAP residual           2.777 s    21.3 %
│  ├─ image_digest                         0.322 s     2.5 %
│  └─ CBOR walk, conditions, policy        0.089 s     0.7 %
└─ hdr_validate                            0.000 s     0.0 %
```

Each row is the median of that phase over the 50 runs, not a decomposition of
any one run: medians are not additive, so the tree is indicative rather than
exact. The artifact term is derived as the remainder and closes it by
construction.

**All of the run-to-run variance is the network.** `total` has a standard
deviation of 115.4 ms across the 50 runs; subtract `payload_fetch` and the
remainder of the update has a standard deviation of **2.6 ms**. The CoAP
payload transfer accounts for essentially the entire spread, and every
cryptographic phase is deterministic far below the level any tier comparison
needs — `sig_verify` varies by 0.8 ms in 2.14 s (0.008 %) and `payload_kem` by
0.7 ms in 2.17 s. Differences between tiers of even a few milliseconds are
therefore resolvable, which the extrapolated noise floor of the single-run
capture could not establish.

**`storage_write` is bimodal by destination slot, and it is not noise.**
Updates alternate slots, and the two populations do not overlap at all:

| Slot | n | Median | Min – Max |
|---|---:|---:|---:|
| 0 | 25 | 3,404.4 ms | 3,403.6 – 3,405.4 ms |
| 1 | 25 | 3,488.4 ms | 3,487.5 – 3,489.3 ms |

Writing to slot 1 costs **84 ms (2.5 %) more** than slot 0, reproducibly, while
within either slot the phase is deterministic to about 2 ms. A single run
samples one of the two values and cannot tell that the other exists — the
3.4875 s previously reported here was a slot-1 install. Any flash-write figure
for this board should name the slot it was measured on.

### 2.2 T2 — Hybrid, PQ encryption (Ed25519 + ML-KEM-768)

[Dongle matrix row 6](DEVICE_NRF52840_DONGLE.md#combination-matrix--nrf52840-dongle).
Manifest 1,643 B → **499 B, byte-identical to T1's plaintext**; payload
121,954 B → 120,812 B installed. **n = 50 consecutive updates.**

| Phase | Median | Min – Max | Bytes | Calls | Chunks |
|---|---:|---:|---:|---:|---:|
| `total` | **11.153 s** | 10.822 – 11.606 s | — | 1 | — |
| `mfst_fetch` | 40.19 ms | 35.18 – 68.86 ms | 1,643 | 1 | 1 |
| `mfst_cose` | 0.080 ms | 0.057 – 0.080 ms | 1,144 | 1 | 1 |
| **`mfst_kem`** | **47.41 ms** | 47.41 – 47.70 ms | — | 1 | — |
| `mfst_aead` | 0.439 ms | 0.438 – 0.439 ms | 499 | 1 | 1 |
| `parse` *(envelope)* | 10.069 s | 9.741 – 10.521 s | — | 1 | — |
| **`sig_verify`** | **2.290 s** | 2.186 – 2.391 s | 111 | 1 | 1 |
| `mfst_digest` | 0.645 ms | 0.645 – 0.646 ms | 380 | 1 | 1 |
| `payload_fetch` *(envelope)* | 7.351 s | 7.029 – 7.778 s | 121,954 | 1 | 1,906 |
| **`payload_kem`** | **82.77 ms** | 82.76 – 83.14 ms | — | 1 | — |
| `payload_aead` | 212.07 ms | 211.96 – 212.42 ms | 120,812 | 3,778 | 3,777 |
| `storage_write` | 3.781 s | 3.780 – 3.782 s | 120,812 | 3,778 | 3,778 |
| `image_digest` | 355.22 ms | 354.54 – 356.23 ms | 241,624 | 2 | 2 |
| `hdr_validate` | 4 µs | 4 – 5 µs | — | 1 | — |

Stack peak **8,792 of 9,216 B (95.4 %)** at `payload_kem` in all 50 runs, with
no excursion at all; **heap 0 B**.

**T2's `sig_verify` is both slower and far noisier than T1's, though both are
Ed25519 on c25519 over an identical 111 B input**: 2.290 s here against
2.1428 s in T1, and a standard deviation of 50.1 ms against T1's 0.17 ms. §4
previously treated this gap as the measurement noise floor; with 50 runs per
tier it is plainly not noise, and the noise floor is roughly 300× smaller than
the effect. The difference must come from something other than the algorithm —
the ML-KEM build pulls in the local wolfSSL tree and a substantially larger
image, so code placement and flash wait states are the obvious suspects — but
this measurement does not isolate the cause.

```
total (median)                            11.153 s   100.0 %
├─ mfst_fetch                              0.040 s     0.4 %
├─ manifest decrypt (cose+kem+aead)        0.048 s     0.4 %
├─ riotboot_hdr_print + ztimer_sleep(1s)   0.996 s     8.9 %   ← artifact
├─ parse                                  10.069 s    90.3 %
│  ├─ sig_verify                           2.290 s    20.5 %   ← Ed25519
│  ├─ payload_fetch                        7.351 s    65.9 %
│  │  ├─ payload_kem                       0.083 s     0.7 %   ← ML-KEM-768
│  │  ├─ storage_write                     3.781 s    33.9 %
│  │  ├─ payload_aead                      0.212 s     1.9 %
│  │  └─ network / CoAP residual           3.275 s    29.4 %
│  ├─ image_digest                         0.355 s     3.2 %
│  └─ CBOR walk, conditions, policy        0.073 s     0.7 %
└─ hdr_validate                            0.000 s     0.0 %
```

Rows are per-phase medians and so do not sum exactly; the artifact term is the
remainder.

**This tier's cost profile is the mirror image of T3's**: the classical
*signature* is now the single largest crypto item (20.3 %), while the
post-quantum *key establishment* is 0.4 % + 0.7 %.

### 2.3 T3 — Hybrid, PQ signature (ML-DSA-44 + X25519)

[Dongle matrix row 10](DEVICE_NRF52840_DONGLE.md#combination-matrix--nrf52840-dongle).
Manifest 2,951 B → 2,859 B; payload 121,514 B → 121,424 B installed.
**n = 47** of 50 attempted; three runs were discarded by the harness for
incomplete reports (see §4).

| Phase | Median | Min – Max | Bytes | Calls | Chunks |
|---|---:|---:|---:|---:|---:|
| `total` | **11.834 s** | 11.606 – 14.766 s | — | 1 | — |
| `mfst_fetch` | 70.12 ms | 64.83 – 104.58 ms | 2,951 | 1 | 1 |
| `mfst_cose` | 0.114 ms | 0.092 – 0.115 ms | 92 | 1 | 1 |
| **`mfst_kem`** | **882.36 ms** | 882.06 – 882.65 ms | — | 1 | — |
| `mfst_aead` | 2.08 ms | 2.08 – 2.08 ms | 2,859 | 1 | 1 |
| `parse` *(envelope)* | 9.880 s | 9.655 – 12.817 s | — | 1 | — |
| **`sig_verify`** | **42.15 ms** | 42.13 – 42.44 ms | 2,469 | 1 | 1 |
| `mfst_digest` | 0.644 ms | 0.643 – 0.644 ms | 380 | 1 | 1 |
| `payload_fetch` *(envelope)* | 9.392 s | 9.167 – 12.329 s | 121,514 | 1 | 1,899 |
| **`payload_kem`** | **2.165 s** | 2.164 – 2.165 s | — | 1 | — |
| `payload_aead` | 213.33 ms | 213.20 – 213.95 ms | 121,424 | 3,796 | 3,795 |
| `storage_write` | 3.793 s | 3.790 – 3.877 s | 121,424 | 3,796 | 3,796 |
| `image_digest` | 357.01 ms | 356.53 – 357.55 ms | 242,848 | 2 | 2 |
| `hdr_validate` | 5 µs | 4 – 5 µs | — | 1 | — |

Stack peak **3,372 of 4,096 B (82.3 %)** at `payload_kem` in all 47 runs, with
no excursion; heap 0 B.

T3 carries the widest `total` range of the five tiers (11.606 – 14.766 s). The
spread is entirely in `payload_fetch`: strip it and the standard deviation of
the remainder is 7.2 ms against 565.0 ms for `total`. The 14.8 s maximum is a
transfer outlier, not a slower update.

```
total (median)                            11.834 s   100.0 %
├─ mfst_fetch                              0.070 s     0.6 %
├─ manifest decrypt (cose+kem+aead)        0.885 s     7.5 %
├─ riotboot_hdr_print + ztimer_sleep(1s)   1.000 s     8.4 %   ← artifact
├─ parse                                   9.880 s    83.5 %
│  ├─ sig_verify                           0.042 s     0.4 %   ← ML-DSA-44
│  ├─ payload_fetch                        9.392 s    79.4 %
│  │  ├─ payload_kem                       2.165 s    18.3 %
│  │  ├─ storage_write                     3.793 s    32.0 %
│  │  ├─ payload_aead                      0.213 s     1.8 %
│  │  └─ network / CoAP residual           3.222 s    27.2 %
│  ├─ image_digest                         0.357 s     3.0 %
│  └─ CBOR walk, conditions, policy        0.088 s     0.7 %
└─ hdr_validate                            0.000 s     0.0 %
```

Rows are per-phase medians and so do not sum exactly; the artifact term is the
remainder.

### 2.4 T4 — Full PQC (ML-DSA-65 + ML-KEM-768)

[Dongle matrix row 19](DEVICE_NRF52840_DONGLE.md#combination-matrix--nrf52840-dongle),
the combination the samr21 physically cannot build.
Manifest 4,892 B → 3,748 B; payload 127,246 B → 126,104 B installed.

**n = 49** of 50 attempted; one run was discarded for an incomplete report.

| Phase | Median | Min – Max | Bytes | Calls | Chunks |
|---|---:|---:|---:|---:|---:|
| `total` | **9.304 s** | 9.133 – 10.273 s | — | 1 | — |
| `mfst_fetch` | 114.41 ms | 110.12 – 170.79 ms | 4,892 | 1 | 1 |
| `mfst_cose` | 0.079 ms | 0.057 – 0.080 ms | 1,144 | 1 | 1 |
| **`mfst_kem`** | **48.12 ms** | 48.12 – 48.42 ms | — | 1 | — |
| `mfst_aead` | 2.87 ms | 2.87 – 2.87 ms | 3,748 | 1 | 1 |
| `parse` *(envelope)* | 8.143 s | 7.972 – 9.098 s | — | 1 | — |
| **`sig_verify`** | **72.47 ms** | 72.44 – 73.01 ms | 3,358 | 1 | 1 |
| `mfst_digest` | 0.645 ms | 0.644 – 0.645 ms | 380 | 1 | 1 |
| `payload_fetch` *(envelope)* | 7.612 s | 7.442 – 8.567 s | 127,246 | 1 | 1,989 |
| **`payload_kem`** | **82.74 ms** | 82.74 – 83.39 ms | — | 1 | — |
| `payload_aead` | 222.84 ms | 222.70 – 223.43 ms | 126,104 | 3,943 | 3,942 |
| `storage_write` | 3.929 s | 3.928 – 4.013 s | 126,104 | 3,943 | 3,943 |
| `image_digest` | 369.87 ms | 369.38 – 370.42 ms | 252,208 | 2 | 2 |
| `hdr_validate` | 5 µs | 4 – 5 µs | — | 1 | — |

> **Stack peak 8,792 B in 47 of 49 runs, but 8,996 B in two — of a 9,216 B
> stack. The worst case observed leaves 220 B of margin, not 424 B.** Both
> excursions are at `payload_kem`, the same phase as the common case. Heap 0 B
> throughout. This is the tightest margin of any tier and the single most
> important consequence of repeating the measurement: the headline figure in
> §3.4 was derived from one run that happened to miss the excursion, and a 4 %
> event is one a single capture is more likely to miss than to catch.

```
total (median)                             9.304 s   100.0 %
├─ mfst_fetch                              0.114 s     1.2 %
├─ manifest decrypt (cose+kem+aead)        0.051 s     0.5 %
├─ riotboot_hdr_print + ztimer_sleep(1s)   0.995 s    10.7 %   ← artifact
├─ parse                                   8.143 s    87.5 %
│  ├─ sig_verify                           0.072 s     0.8 %   ← ML-DSA-65
│  ├─ payload_fetch                        7.612 s    81.8 %
│  │  ├─ payload_kem                       0.083 s     0.9 %   ← ML-KEM-768
│  │  ├─ storage_write                     3.929 s    42.2 %
│  │  ├─ payload_aead                      0.223 s     2.4 %
│  │  └─ network / CoAP residual           3.377 s    36.3 %
│  ├─ image_digest                         0.370 s     4.0 %
│  └─ CBOR walk, conditions, policy        0.088 s     0.9 %
└─ hdr_validate                            0.000 s     0.0 %
```

Rows are per-phase medians and so do not sum exactly; the artifact term is the
remainder.

**With the crypto this cheap, the update is now bound entirely by I/O:**
storage and network are 78 % of it, and the artificial 1 s sleep is a bigger
share (11 %) than all asymmetric cryptography combined (2.2 %).

---

### 2.5 T5 — Classical with ECDSA (ES256 + X25519)

Not a tier of its own: ES256 is equally broken by Shor's algorithm and sits
inside the classical tier. It exists as the **control that separates the
library from the algorithm** — a classical signature running on wolfCrypt
rather than c25519. Manifest 591 B → 499 B, byte-identical to T1's; payload
113,990 B → 113,900 B installed.

**n = 48** of 50 attempted; two runs were discarded for incomplete reports.

| Phase | Median | Min – Max | Bytes | Calls | Chunks |
|---|---:|---:|---:|---:|---:|
| `total` | **11.700 s** | 11.479 – 13.806 s | — | 1 | — |
| `mfst_fetch` | 15.03 ms | 14.15 – 36.44 ms | 591 | 1 | 1 |
| `mfst_cose` | 0.113 ms | 0.091 – 0.114 ms | 92 | 1 | 1 |
| `mfst_kem` | 882.30 ms | 882.01 – 882.56 ms | — | 1 | — |
| `mfst_aead` | 0.432 ms | 0.431 – 0.432 ms | 499 | 1 | 1 |
| `parse` *(envelope)* | 9.806 s | 9.586 – 11.914 s | — | 1 | — |
| **`sig_verify`** | **450.81 ms** | 450.80 – 451.36 ms | 111 | 1 | 1 |
| `mfst_digest` | 0.643 ms | 0.643 – 0.644 ms | 380 | 1 | 1 |
| `payload_fetch` *(envelope)* | 8.932 s | 8.712 – 11.040 s | 113,990 | 1 | 1,782 |
| `payload_kem` | 2.164 s | 2.164 – 2.165 s | — | 1 | — |
| `payload_aead` | 199.94 ms | 199.85 – 200.42 ms | 113,900 | 3,561 | 3,560 |
| `storage_write` | 3.579 s | 3.536 – 3.622 s | 113,900 | 3,561 | 3,561 |
| `image_digest` | 334.54 ms | 334.10 – 335.10 ms | 227,800 | 2 | 2 |
| `hdr_validate` | 4 µs | 4 – 5 µs | — | 1 | — |

```
total (median)                            11.700 s   100.0 %
├─ mfst_fetch                              0.015 s     0.1 %
├─ manifest decrypt (cose+kem+aead)        0.883 s     7.5 %
├─ riotboot_hdr_print + ztimer_sleep(1s)   0.996 s     8.5 %   ← artifact
├─ parse                                   9.806 s    83.8 %
│  ├─ sig_verify                           0.451 s     3.9 %   ← ES256
│  ├─ payload_fetch                        8.932 s    76.3 %
│  │  ├─ payload_kem                       2.164 s    18.5 %
│  │  ├─ storage_write                     3.579 s    30.6 %
│  │  ├─ payload_aead                      0.200 s     1.7 %
│  │  └─ network / CoAP residual           2.989 s    25.5 %
│  ├─ image_digest                         0.335 s     2.9 %
│  └─ CBOR walk, conditions, policy        0.089 s     0.8 %
└─ hdr_validate                            0.000 s     0.0 %
```

**This is the only tier whose stack peak is the signature, and the only one
whose stack peak is not a constant.** The deepest point is `sig_verify` in all
48 runs, but at four distinct depths:

| Depth | Runs |
|---:|---:|
| 3,480 B | 25 |
| 3,660 B | 3 |
| 3,676 B | 3 |
| **3,684 B** | **17** |

The single-run capture reported 3,480 B (56.6 %); the true worst case is
**3,684 B (60.0 %)**, and the deepest value occurs in a third of runs rather
than rarely. Every other tier's peak is either fixed or has a single rare
excursion, so this is a property of the primitive, not of the harness:
wolfCrypt's ECDSA verification is **data-dependent in stack depth**, as the
scalar multiplication's path varies with the signature under test, and each run
verifies a different signature. Ed25519 and ML-DSA show no such behaviour.
ES256 remains cheaper in time than Ed25519 and deeper in stack, but the gap is
larger than one run suggested: 3,684 B against T1's 2,756 B.

---

## 3. Findings

### 3.1 The signature speed-up splits into 4.9× library × 10.7× algorithm

All four signature algorithms verify **the same 111 B COSE object** (T1, T2 and
T5 over a byte-identical 499 B manifest; T3/T4 over their own larger ones):

| | Ed25519 (T1, T2) | **ES256 (T5)** | ML-DSA-44 (T3) | ML-DSA-65 (T4) |
|---|---:|---:|---:|---:|
| Library | **c25519** | **wolfCrypt** | wolfCrypt | wolfCrypt |
| `sig_verify` | 2,142.8 / 2,290.0 ms | **450.8 ms** | **42.1 ms** | **72.5 ms** |
| vs Ed25519 | — | **4.9× faster** | **52.3× faster** | **30.4× faster** |
| **vs ES256 — same library** | — | — | **10.7× faster** | **6.2× faster** |
| Share of update | 16.4 % / 20.5 % | 3.9 % | 0.4 % | 0.8 % |
| Signature on the wire | 64 B | 64 B | 2,420 B | 3,309 B |
| Is it the stack peak? | no | **yes, 3,480–3,684 B** | no | no |

**T5 was run precisely to break the confound**, and it does so cleanly: T1 and
T5 share the manifest, the COSE object, the KEM, the manifest buffer, the worker
stack *and the pinned wolfSSL tree*. The only difference is which library
verifies. The 52.3× factorises almost exactly:

```
Ed25519 → ML-DSA-44 :  2,204.8 ms / 42.1 ms  =  52.3×
                    =  (2,204.8 / 450.8)  ×  (450.8 / 42.1)
                    =        4.9×         ×       10.7×
                        library effect       algorithm effect
```

(Ed25519 taken as the mean of its two measurements, 2,204.8 ms; the pair spans
5.8 %, the CPU-bound noise floor — see [§4](#4-methodology-validation).)

**Both halves are real, and the algorithmic half is the larger one:**

- **4.9× is implementation.** Ed25519 runs on c25519 (dlbeer) — compact,
  byte-serial, chosen for code size, and *unavoidable* here: its `fprime_*`
  symbols collide with wolfCrypt's `fe_low_mem.c`, so the wolfCrypt curve25519
  backend cannot be linked alongside libcose's c25519 signature backend
  (`sys/suit/encrypt/decrypt.c:63-71`). Moving a classical signature to
  wolfCrypt buys 4.9× and nothing else changes.
- **10.7× is the algorithm.** ES256 and ML-DSA-44 both run on wolfCrypt at
  comparable security levels (P-256 ≈ 128-bit classical; ML-DSA-44 = NIST
  category 2), and lattice verification is still an order of magnitude faster —
  NTT-based polynomial arithmetic instead of big-integer modular arithmetic over
  a curve.

Going up a security category costs 1.72× (ML-DSA-44 → -65, 42.1 → 72.5 ms), and
ML-DSA-65 is still **6.2× faster than ES256**.

**How to state this in the thesis:** "ML-DSA verifies ~50× faster than Ed25519"
is true but implementation-loaded, and should never be quoted without its
decomposition. The claim that survives every control is:

> **Post-quantum signature verification is roughly an order of magnitude cheaper
> than elliptic-curve verification measured in the same library, on the same
> core, over the same manifest.** Its real costs are wire size and ROM, not time.

### 3.2 ML-KEM-768 decapsulation is 18.5× faster than X25519

The clean CPU measurement (`mfst_kem`, taken with the network idle), each
algorithm measured in two independent tiers:

| | X25519 (T1, T3) | ML-KEM-768 (T2, T4) |
|---|---:|---:|
| `mfst_kem` | 882.7 / 882.1 ms | **47.5 / 48.1 ms** |
| Within-algorithm spread | 0.07 % | 1.4 % |
| **Ratio** | — | **18.5× faster** (18.3–18.6) |
| Recipient overhead on the wire | 92 B | 1,144 B |

**T1 vs T2 isolates this axis perfectly.** Both tiers produce a byte-identical
499 B manifest plaintext with the same Ed25519 signature; only the recipient
structure differs. Everything below is attributable to the KEM alone:

| | T1 X25519 | T2 ML-KEM-768 | Δ |
|---|---:|---:|---:|
| `mfst_kem` | 882.7 ms | 47.5 ms | **−835.2 ms (18.6× faster)** |
| `payload_kem` | 2,165.3 ms | 82.8 ms | **−2,082.5 ms** |
| Manifest on the wire | 591 B | 1,643 B | **+1,052 B** |
| Payload header | 74 B | 1,126 B | **+1,052 B** |
| Payload installed | 109,496 B | 120,812 B | +11,316 B |
| **Stack peak** (worst) | 2,964 B | 8,792 B | **+5,828 B** |
| `total` | 13.037 s | 11.153 s | **−1.884 s (−14.4 %)** |

Two derivations run per update, so the KEM axis alone saves ~2.9 s of wall time
— while costing 1,052 B more per encrypted object, twice, and **6 KB more
stack** ([§3.4](#34-ml-kem-uses-8792-b-of-stack--and-8996-b-in-the-worst-case-leaving-220-b)).

The same caveat as §3.1 applies with even more force: X25519 here is
**c25519**, chosen because wolfCrypt's curve25519 cannot be linked alongside
libcose's c25519 signature backend. This is a comparison of *what RIOT's SUIT
path can actually link*, which is the operationally relevant question, but it is
not a statement about X25519 as an algorithm.

**Both post-quantum axes are therefore cheaper in time and dearer in space on
this platform** — the reverse of the intuition the space-only tables in
[CRYPTO_TIERS.md](CRYPTO_TIERS.md) invite.

### 3.3 Full PQC is 3.73 s faster than Full Classical

T4 ships far more data than T1, and is still much faster:

| | T1 | T4 | Δ |
|---|---:|---:|---:|
| Manifest on the wire | 591 B | 4,892 B | **+4,301 B (+728 %)** |
| Payload installed | 109,496 B | 126,104 B | **+16,608 B (+15.2 %)** |
| `total` | 13.037 s | **9.304 s** | **−3.733 s (−28.6 %)** |

Summing the measured per-phase deltas — no modelling, just T4 minus T1:

| Phase | Δ |
|---|---:|
| `sig_verify` | **−2,070.1 ms** |
| `payload_kem` | **−2,082.5 ms** |
| `mfst_kem` | **−835.3 ms** |
| `mfst_cose` | −0.0 ms |
| **Total crypto saving** | **−4,988.2 ms** |
| network / CoAP residual | +600.2 ms |
| `storage_write` | +482.9 ms |
| `mfst_fetch` | +99.2 ms |
| `image_digest` | +47.7 ms |
| `payload_aead` | +28.1 ms |
| `mfst_aead`, `mfst_digest`, CBOR walk, sleep | −3.0 ms |
| **Total cost of the bigger artifacts** | **+1,255.1 ms** |
| **Sum of deltas** | **−3,733.0 ms** |
| **Observed** (13.037 → 9.304 s) | **−3,733.0 ms** |

The decomposition closes exactly, though that is now partly by construction:
both columns are per-phase medians over the same runs, so the residual is not
an independent check the way it was for a single pair of runs. The substantive
claim is unchanged — the tier difference is two effects, cheaper asymmetric
crypto and more bytes moved, with nothing unattributed.

### 3.4 ML-KEM uses 8,792 B of stack — and 8,996 B in the worst case, leaving 220 B

**This is the measurement that link-time analysis provably could not produce**,
and the one the whole `SUIT_PERF` exercise was built for. It is also the
section the repeats changed most: two of the five tiers turned out to have a
worst case deeper than the single run recorded.

| Tier | KEM | Peak phase | Budget | Common | **Worst observed** | Free (worst) |
|---|---|---|---:|---:|---:|---:|
| T1 Classical | X25519 | `payload_kem` | 6,144 B | 2,756 B (49/50) | 2,964 B | 48 % / 3,180 B |
| T5 ECDSA | X25519 | **`sig_verify`** | 6,144 B | 3,480 B (25/48) | **3,684 B** | 60 % / 2,460 B |
| T3 PQ-sig | X25519 | `payload_kem` | 4,096 B | 3,372 B (47/47) | 3,372 B | 82 % / 724 B |
| **T2 PQ-enc** | ML-KEM-768 | `payload_kem` | 9,216 B | 8,792 B (50/50) | 8,792 B | 95 % / 424 B |
| **T4 Full PQC** | ML-KEM-768 | `payload_kem` | 9,216 B | 8,792 B (47/49) | **8,996 B** | **98 % / 220 B** |

[FINDINGS.md §4](FINDINGS.md) predicted **~8.5–9 KB** for wolfCrypt's ML-KEM
decapsulation chain from `-fstack-usage` analysis, and the app Makefile forces
`SUIT_WORKER_STACKSIZE=9216` for any `ml-kem-%` build on that basis. **Measured:
8,792 B typically and 8,996 B at worst — still inside the predicted band, but
with 220 B to spare rather than 424 B.**

**The excursions are rare and therefore easy to miss.** T4 reached 8,996 B in 2
of 49 runs and T1 reached 2,964 B in 1 of 50 — roughly 4 % and 2 % of updates,
both at `payload_kem`, the same phase as the common case. A single capture is
far more likely to miss such an event than to catch it, which is precisely why
the previous edition of this table reported the common case as the peak. T5 is
the opposite situation and is discussed in §2.5: its depth is data-dependent
and takes four distinct values, with the deepest occurring in a third of runs.

**T2 and T4 agree to the byte** — 8,792 B with an Ed25519 signature and with an
ML-DSA-65 one. The peak is therefore set *entirely* by the ML-KEM decapsulation
path and is independent of the signature algorithm, exactly as predicted by
ML-DSA's verify state being a static `MlDsaKey` rather than stack. Holding the
KEM fixed instead (T3 vs T2, both on the local wolfSSL tree) isolates the cost
of the post-quantum KEM itself: **+5,420 B of stack**.

Consequences:

1. **The 9,216 B forcing is not conservative padding — it is barely enough.**
   A 8,192 B stack would overflow by 600 B in the common case and by 804 B in
   the worst observed one, and on a Cortex-M0+ without an MPU that would
   silently corrupt `.bss` rather than fault. At 220 B of remaining margin, the
   9,216 B figure should be treated as a floor that has been empirically
   probed, not as a bound with room in it.
2. **The samr21's Full-PQC infeasibility is confirmed as real, not an artifact
   of a padded estimate.** That board must find 9,216 B of stack out of 32 KB
   total, on top of a 5,056 B manifest buffer.
3. **The deepest point is `payload_kem` in all four tiers** — decapsulation
   inside the fetch callback (worker → parse → fetch → transport callback →
   `derive_cek`), never signature verification, whose ML-DSA state is a static
   `MlDsaKey`. Stack budgeting for SUIT should target the fetch path. **T5 is
   the sole exception** (see below), so the rule is "the fetch path, unless the
   signature backend is wolfCrypt ECDSA".

**T5 adds a fifth data point: the signature can be the peak.** At 3,480 B
typically and **3,684 B at worst**, its `sig_verify` outranks the X25519
`payload_kem` chain (2,756–2,964 B in T1, same 6,144 B stack). wolfCrypt's
ECDSA is cheaper in time than c25519's Ed25519 but deeper in stack — so "budget
the fetch path" holds for four of five configurations, not all five. It is also
the only peak in the five tiers that is **data-dependent**, taking four
distinct values across 48 runs (§2.5), so it is the one that most needs a
worst-case rather than a typical figure.

> **Correction.** This section previously attributed the T1→T3 **+616 B** step to
> the wolfSSL source tree: `makefiles/suit.base.inc.mk:55-56` switches wolfSSL to
> the local checkout at `dist/tools/suit/ml-dsa-example/wolfssl` whenever
> `SUIT_KEY_ALGO=ml-dsa-%` **or** `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-%`, making
> T1 and T5 the only tiers on RIOT's pinned `pkg/wolfssl`. The supporting evidence
> was that `payload_aead` throughput grouped T2/T3/T4 apart from T1.
>
> **T5 falsifies that evidence.** T5 is on the *pinned* tree, yet its
> `payload_aead` throughput (570.0 kB/s) groups with T2/T3/T4 (566.0–569.8) and
> not with T1 (543.0). T1 is simply the outlier among five runs, for a reason
> that is not the wolfSSL tree.
>
> The **+616 B stack step remains unexplained.** The tree difference is still a
> candidate — the shared HKDF/ChaCha frames in `derive_cek` do come from
> wolfCrypt — but it now has no corroboration, and T5 cannot test it because its
> peak is elsewhere. A T1-configuration build forced onto the local tree would
> settle it.

### 3.5 Zero heap with ML-KEM running — the `WOLFSSL_NO_MALLOC` fix is confirmed at runtime

`heap_hwm = 0` in **both** ML-KEM tiers, with **two ML-KEM-768 decapsulations
executed per run** — four in total, on two different builds.

This closes out [FINDINGS.md §4](FINDINGS.md)'s wolfCrypt discovery. The
original bug was that `wc_mlkem.c` unconditionally `XMALLOC`s multi-KB scratch
buffers — invisible to `arm-none-eabi-size` and `nm`, and the cause of
`CEK derivation failed: -125` on samr21 despite apparent spare RAM. The fix
(`WOLFSSL_NO_MALLOC` + the `*_SMALL_MEM` knobs) was until now a **configuration
claim**: the settings were set, and the symptom stopped.

It is now an **empirical fact on hardware**: the post-quantum key
establishment path allocates nothing, and the memory it needs is exactly the
8,792 B of stack measured in §3.4. T1 and T3's zeros proved nothing on their
own, since neither ran ML-KEM.

### 3.6 `payload_kem` is wall-clock under load, and is **not** cross-tier comparable

> **Correction.** After T3 this document stated that `payload_kem` was
> comparable across tiers. T4 disproves that.

`mfst_kem` and `payload_kem` run identical code, yet differ per tier:

| Tier | KEM | `mfst_kem` | `payload_kem` | Inflation |
|---|---|---:|---:|---:|
| T1 | X25519 | 882.7 ms | 2,165.5 ms | **2.453×** |
| T3 | X25519 | 882.1 ms | 2,164.2 ms | **2.454×** |
| T2 | ML-KEM-768 | 47.5 ms | 82.8 ms | **1.743×** |
| T4 | ML-KEM-768 | 48.1 ms | 82.7 ms | **1.719×** |

The inflation is **reproducible per algorithm to within 1.4 %** — 2.453/2.454
for X25519, 1.743/1.719 for ML-KEM — and **differs sharply between
algorithms**. It is therefore neither a constant factor nor a constant offset,
and cannot be corrected for.

The cause is *where* the two run: `mfst_kem` executes with the network idle,
while `payload_kem` executes inside the CoAP blockwise callback with the
transfer in flight, where gnrc's networking threads — higher priority than the
SUIT worker at `THREAD_PRIORITY_MAIN-1` — preempt it. `ztimer_now()` measures
wall clock. A callback that blocks for 2.2 s (X25519) plausibly crosses CoAP
retransmission timers that an 83 ms one (ML-KEM) never reaches, which would
explain why the longer operation is penalised proportionally more.

**Rule: use `mfst_kem` for every cross-algorithm KEM comparison.**
`payload_kem` measures the real cost *in situ* — useful for end-to-end latency,
which is why it is included in the §3.3 budget — but it is not a measure of the
algorithm.

### 3.7 Where the time goes, per tier

| | T1 Classical | T2 PQ-enc | T3 PQ-sig | T4 Full PQC | T5 ECDSA |
|---|---:|---:|---:|---:|---:|
| Asymmetric crypto | **5.192 s (40.2 %)** | 2.397 s (21.5 %) | 3.088 s (26.7 %) | **0.203 s (2.2 %)** | 3.497 s (29.2 %) |
| — of which signature | 2.143 s | **2.267 s** | 0.042 s | 0.072 s | 0.451 s |
| — of which key establishment | 3.048 s | 0.130 s | **3.046 s** | 0.131 s | **3.046 s** |
| Symmetric + hashing | 0.523 s (4.0 %) | 0.567 s (5.1 %) | 0.573 s (4.9 %) | 0.596 s (6.6 %) | 0.535 s (4.5 %) |
| Network + storage | 6.108 s (47.3 %) | 7.122 s (63.7 %) | 6.776 s (58.5 %) | 7.098 s (78.1 %) | 6.878 s (57.3 %) |
| 1 s sleep artifact | 0.996 s (7.7 %) | 0.997 s (8.9 %) | 0.996 s (8.6 %) | 0.996 s (11.0 %) | 0.996 s (8.3 %) |

The two middle rows are the cleanest statement of the whole exercise: **each
hybrid tier collapses exactly one of them** and leaves the other untouched.
T2 kills key establishment (3.048 → 0.130 s) while its signature cost stays
classical; T3 kills the signature (2.143 → 0.042 s) while its key establishment
stays classical. Only T4 collapses both.

T5 shows the *classical* ceiling for comparison: changing library alone takes
the signature from 2.143 s to 0.451 s, but its key establishment — X25519 on
c25519, to three decimal places identical to T1's and T3's 3.046 s — does not
move at all. **The single largest remaining cost in any non-ML-KEM
configuration is X25519**, not the signature.

The progression is the story in one line: **the classical tier is crypto-bound,
the full post-quantum tier is I/O-bound.** In T4 the artificial 1 s sleep costs
five times as much as all asymmetric cryptography combined.

### 3.8 The PQ-signature tier alone is a net 1.20 s faster

The same accounting as §3.3, but for T3 against T1 — isolating the signature
axis, since both tiers use X25519. T3 ships considerably more data:

| | T1 | T3 | Δ |
|---|---:|---:|---:|
| Manifest on the wire | 591 B | 2,951 B | **+2,360 B (+399 %)** |
| Payload installed | 109,496 B | 121,424 B | **+11,928 B (+10.9 %)** |

The manifest delta matches the ML-DSA-44 signature exactly (2,420 B vs 64 B =
+2,356 B, plus 4 B of CBOR framing). The payload delta is the ML-DSA verifier
code and its 1,312 B public key inside the firmware image.

Costing that extra data at the tiers' own measured throughputs:

| Extra work | Cost |
|---|---:|
| +11,928 B through storage @ 32.0 kB/s | +0.373 s |
| +11,928 B over the network @ 37.7 kB/s | +0.316 s |
| +11,928 B hashed twice @ 680.2 kB/s | +0.035 s |
| +11,928 B through the AEAD @ 569.2 kB/s | +0.021 s |
| +2,360 B of manifest (fetch + AEAD) | +0.057 s |
| **Total cost of the bigger artifacts** | **+0.802 s** |
| **Saving from faster verification** | **−2.101 s** |
| **Predicted net** | **−1.299 s** |
| **Observed net** (13.037 → 11.834 s) | **−1.202 s** |

Predicted and observed agree to **97 ms (8.1 % of the net)** — a looser fit
than the 18 ms the single-run pair produced. That earlier agreement was
partly luck: the model is driven by a network throughput that varies by ±2 %
between runs, and one pair of runs cannot show that. The direction and
magnitude hold; the third significant figure does not.

### 3.9 Storage is the largest real cost, at ~32 B per write

`storage_write` is the biggest genuine cost in every run (3.49–4.01 s, 27–44 %
of the update) at a flat **31.3–32.0 kB/s**, and it is called **twice per
transport block** — 1,711 blocks → 3,420 writes; 1,782 → 3,561; 1,899 → 3,796;
1,906 → 3,778; 1,989 → 3,943.

This is the 16-byte tag-lag split in `_feed_ciphertext()`
(`sys/suit/encrypt/payload_decrypt.c`): each 64 B block is forwarded as a ~16 B
flush of the withheld tail plus a ~48 B remainder, averaging **32.0 B per write
in all five runs**. Coalescing the two into one forwarded chunk is the single
highest-value optimisation this data suggests: it targets the phase that is now
**44 % of a full post-quantum update**. Quantifying it against a plaintext
(`SUIT_FIRMWARE_ENCRYPT=0`) run, where no lag buffer exists, would isolate the
cost.

### 3.10 Two tier-invariant inefficiencies

- **The image digest runs twice** — 2 calls over 2× the image size, as SUIT
  validates the installed payload during install and again in the validate
  sequence. 321/355/357/370 ms, i.e. 2× the necessary work in every tier.
- **A hard-coded 1 s sleep** (`ztimer_sleep(ZTIMER_MSEC, 1 * MS_PER_SEC)` in
  `sys/suit/transport/worker.c`, upstream RIOT) is 7.7–11.0 % of update latency
  and *rises* as a share in the faster PQ tiers. Tier deltas are unaffected;
  **absolute latency figures should subtract it**.

### 3.11 Confirmations of previously space-only claims

Every wire-format figure in [CRYPTO_TIERS.md §2](CRYPTO_TIERS.md) that these
runs could touch is now confirmed on hardware:

| Claim | Measured |
|---|---|
| X25519 COSE recipient overhead ~92 B | `mfst_cose` = **92 B** (T1, T3) ✅ |
| **ML-KEM-768 recipient overhead ~1,144 B** | `mfst_cose` = **1,144 B** (T2, T4) — exact, twice ✅ |
| X25519 payload header 74 B | `header 74 bytes` (T1, T3) ✅ |
| **ML-KEM-768 payload header 1,126 B** | `header 1126 bytes` (T2, T4) — exact, twice ✅ |
| Both KEM overheads apply **twice per update** | manifest +1,052 B **and** payload header +1,052 B, T1→T2 ✅ |
| ML-DSA-44 signature 2,420 B | manifest grew **+2,360 B** vs Ed25519's 64 B ✅ |
| ML-DSA-65 signature 3,309 B | manifest grew **+4,301 B** — signature +3,245 B, KEM +1,052 B ✅ |
| ML-DSA-44 buffer 3,072 (+128) | 3,200, and a 2,951 B manifest fits ✅ |
| ML-DSA-65 buffer 3,840 (+1,216) | 5,056, and a 4,892 B manifest fits ✅ |
| **ML-KEM stack need ~8.5–9 KB** ([FINDINGS.md §4](FINDINGS.md), `-fstack-usage`) | **8,792 B measured** — inside the band ✅ |
| **`WOLFSSL_NO_MALLOC` keeps ML-KEM off the heap** ([FINDINGS.md §4](FINDINGS.md)) | heap high-water **0 B with ML-KEM running** ✅ |

The last two were the project's only load-bearing claims that no static
analysis could settle. Both hold.

---

### 3.12 The whole run-to-run spread is the network — the device is deterministic

Every tier tells the same story. Subtracting `payload_fetch` from `total`
removes essentially all the variance:

| Tier | `total` stdev | stdev without `payload_fetch` |
|---|---:|---:|
| T1 | 115.4 ms | **2.6 ms** |
| T2 | 193.4 ms | 51.1 ms |
| T3 | 565.0 ms | **7.2 ms** |
| T4 | 220.6 ms | **10.4 ms** |
| T5 | 326.0 ms | **3.4 ms** |

The device-side work is repeatable to a few milliseconds out of 9–13 seconds;
the CoAP transfer over CDC-ECM is what moves. T2 is the only exception, and its
extra 51 ms is its anomalous `sig_verify` (§2.2), not transport.

Two consequences. First, **tier comparisons are limited by the transport, not
by the instrumentation**: `sig_verify` differences of a millisecond are
resolvable, but `total` differences below ~200 ms are not, so quote per-phase
figures rather than `total` when the effect is small. Second, the noise floor
§4 previously extrapolated from one pair of Ed25519 runs was far too
pessimistic for the compute phases and roughly right for `total`.

### 3.13 The 84 ms slot-1 penalty is one flash page erase

`storage_write` is bimodal in four of the five tiers, splitting cleanly by the
slot being written, with no overlap between the two populations:

| Tier | Slot 0 | Slot 1 | Δ |
|---|---:|---:|---:|
| T1 | 3,404.4 ms | 3,488.4 ms | +84.0 ms |
| T2 | 3,781.1 ms | 3,780.8 ms | **−0.3 ms** |
| T3 | 3,791.8 ms | 3,875.6 ms | +83.8 ms |
| T4 | 3,928.6 ms | 4,012.4 ms | +83.8 ms |
| T5 | 3,536.7 ms | 3,620.6 ms | +83.9 ms |

**T2 is not an anomaly — it is the control that confirms the mechanism.**
Slot 0 starts at `0x4000`, which is 4 KB-aligned; slot 1 starts at `0x71800`,
which is 2,048 B into a page. An image therefore usually spans one more flash
page in slot 1 than in slot 0, and one nRF52840 page erase costs ~85 ms.
Counting pages predicts the observation exactly, including the negative case:

| Tier | Payload | Pages, slot 0 | Pages, slot 1 | Predicted | Observed |
|---|---:|---:|---:|---:|---:|
| T1 | 109,496 B | 27 | 28 | +85 ms | +84.0 ms |
| T2 | 120,812 B | 30 | 30 | **0 ms** | **−0.3 ms** |
| T3 | 121,424 B | 30 | 31 | +85 ms | +83.8 ms |
| T4 | 126,104 B | 31 | 32 | +85 ms | +83.8 ms |
| T5 | 113,900 B | 28 | 29 | +85 ms | +83.9 ms |

T2's payload is the one size at which both slots need 30 pages, and it is
exactly the tier that shows no penalty. Within a slot the phase is
deterministic to about 2 ms.

This is invisible to a single run, which samples one slot and reports it as
*the* flash-write cost — the figure previously given for each tier here was
whichever slot that run happened to install to. It also means **`storage_write`
is not a pure throughput measure**: it carries a quantised erase cost that
depends on where the image lands, so the ~32 kB/s figures in §1 are averages
over a two-valued distribution rather than a rate.


---

## 4. Methodology validation

The tier-invariant phases were predicted to stay flat. Across five runs with
four signature algorithms, two KEMs, manifests from 591 B to 4,892 B and a
17 KB payload spread:

All figures below are medians over the per-tier repeats (n = 47–50).

| Control | T1 | T2 | T3 | T4 | T5 | Spread |
|---|---:|---:|---:|---:|---:|---:|
| `mfst_digest` (380 B) | 643 µs | 645 µs | 644 µs | 645 µs | 643 µs | **0.31 %** |
| `image_digest` throughput | 679.7 kB/s | 680.2 kB/s | 680.2 kB/s | 681.9 kB/s | 680.9 kB/s | **0.32 %** |
| 1 s sleep artifact | 999.8 ms | 996.0 ms | 1000.0 ms | 995.2 ms | 996.0 ms | **0.48 %** |
| `storage_write` throughput | 31.8 kB/s | 32.0 kB/s | 32.0 kB/s | 32.1 kB/s | 31.8 kB/s | **0.94 %** |
| `payload_aead` throughput | 562.4 kB/s | 569.7 kB/s | 569.2 kB/s | 565.9 kB/s | 569.7 kB/s | **1.3 %** |
| *Host-dep.* network throughput | 39.4 kB/s | 36.9 kB/s | 37.7 kB/s | 37.3 kB/s | 38.1 kB/s | **6.8 %** |
| CBOR/condition walk *(derived)* | 89.2 ms | 72.6 ms | 88.5 ms | 88.3 ms | 88.6 ms | **22.8 %** |

**Four of the six measured controls tightened against the single-run
edition**, which is what repeating should do: `payload_aead` went from 5.0 %
to 1.3 %, network throughput from 19.1 % to 6.8 %, `storage_write` from 1.9 %
to 0.94 %. The 1 s sleep artifact and `image_digest` loosened marginally
(0.10 → 0.48 %, 0.16 → 0.32 %) because a median over 50 runs is a fairer
estimate than one sample.

**The CBOR/condition walk is no longer usable as a control, and this is an
artifact of the method, not a change in the device.** It is not measured
directly — it is `parse` minus its children — so with medians it absorbs any
phase whose distribution is skewed. T2's is, because of its noisy `sig_verify`
(§2.2), which drags the residual down to 72.6 ms. The other four tiers agree
to 0.8 %. Treat this row as a decomposition residual, not a control.

Because the hybrids and the ES256 control each hold one thing fixed, **every
algorithm was measured at least twice**:

| Repeat measurement | | | | Drift |
|---|---:|---:|---:|---:|
| `payload_kem`, ML-KEM-768 (T2, T4) | 82.77 | 82.74 ms | | **0.03 %** |
| `payload_kem`, X25519 (T1, T3, T5) | 2,165.3 | 2,164.5 | 2,164.4 ms | **0.04 %** |
| `mfst_kem`, X25519 (T1, T3, T5) | 883.4 | 882.4 | 882.3 ms | **0.13 %** |
| `mfst_fetch`, 591 B (T1, T5) | 15.19 | 15.03 ms | | **1.1 %** |
| `mfst_aead`, 499 B (T1, T2, T5) | 438 | 439 | 432 µs | **1.6 %** |
| `mfst_kem`, ML-KEM-768 (T2, T4) | 47.41 | 48.12 ms | | **1.5 %** |
| **Stack peak, ML-KEM-768 (T2, T4)** | 8,792 | 8,792 B (common) | 8,996 B (T4 worst) | **2.3 %** |
| `sig_verify`, Ed25519 (T1, T2) | 2,142.8 | 2,290.0 ms | | **6.9 %** |

The instrumentation is sound and the effects in §3 — 4.9× to 52× — are real.

Two controls remain looser, both explainable, neither affecting a conclusion:

- **Network throughput (6.8 %)** is the only figure depending on the host, the
  USB link and CoAP timing rather than the device. It appears in §3 only as a
  *measured* residual, never as a modelled rate — except in §3.8, where using
  it as a rate is now visibly the weakest step in that model. **Do not quote it
  as a device characteristic.**
- **`sig_verify` for Ed25519 (6.9 %)** is the one control the repeats
  *promoted* rather than resolved. T1 and T2 verify the same 111 B COSE object
  over the same 499 B manifest with the same c25519 code, and differ by 147 ms.
  With 50 runs each and standard deviations of 0.17 ms and 50.1 ms, this is far
  outside any plausible measurement error — the single-run edition could
  reasonably call it noise, and that is no longer available. The cause must lie
  outside the algorithm; code placement, flash wait states and cache alignment
  between two very different binaries are the candidates, and none is isolated
  by this experiment. See §2.2.

> **Correction, twice over.** With four runs, `payload_aead` appeared to group
> T2/T3/T4 apart from T1, which was attributed to T1 being the only tier on
> RIOT's pinned `pkg/wolfssl`. **T5 refuted that**: T5 is on the pinned tree and
> grouped with T2/T3/T4 rather than with T1, so the wolfSSL tree was not the
> cause. **The repeats then dissolved the effect itself.** T1 now measures
> 562.4 kB/s against 565.9–569.7 for the others — a 1.3 % spread rather than
> 5.0 %, and T1's own figure moved by 3.6 % between the single run and the
> median of 50. There was never a grouping to explain: the apparent one was a
> single sample of a phase that varies by a few percent, on a T1 binary that has
> since changed by 108 B. The lesson is the section's own: an unexplained
> grouping across five single runs is a hypothesis, not a finding.

**Empirical noise floors: ~6 % for CPU-bound phases across binaries, ~0.5 % for
hardware-bound and repeated-within-algorithm measurements, and no useful bound
on the host-dependent network residual.**

---

## 5. Capture notes

### All five runs

`PERFCFG` was not captured in any of them: the config prints at boot, and a
successful update reboots through a USB re-enumeration that kills picocom
(`FATAL: read zero bytes from port`) before the new slot's banner arrives.
**Fixed** — the `suit_perf` shell command now prints the config line first, so
reconnecting after the reboot and running `suit_perf` recovers it
([runbook step 8](PERF_RUNBOOK_NRF52840.md)).

No run was repeated for this, because almost every field is a compile-time
constant that is independently recoverable. **Reconstructed, not captured:**

```
PERFCFG,nrf52840dongle,ed25519,x25519,768,192,0,6144,32              # T1
PERFCFG,nrf52840dongle,ed25519,ml-kem-768,1856,1216,0,9216,32        # T2
PERFCFG,nrf52840dongle,ml-dsa-44,x25519,3200,192,0,4096,1312         # T3
PERFCFG,nrf52840dongle,ml-dsa-65,ml-kem-768,5056,1216,?,9216,1952    # T4
PERFCFG,nrf52840dongle,es256,x25519,768,192,0,6144,64                # T5
```

| Field | T1 | T2 | T3 | T4 | T5 | Evidence |
|---|---|---|---|---|---|---|
| sig / kem | `ed25519`/`x25519` | `ed25519`/`ml-kem-768` | `ml-dsa-44`/`x25519` | `ml-dsa-65`/`ml-kem-768` | `es256`/`x25519` | in all 14 captured `PERF` lines |
| `manifest_buf` | 768 | 1,856 | 3,200 | 5,056 | 768 | `make info-debug-variable-SUIT_MANIFEST_BUFSIZE` per tier |
| `fw_hdr` | 192 | 1,216 | 192 | 1,216 | 192 | `SUIT_FW_ENC_HDR_LEN` branches in `sys/include/suit/pq_scratch.h` |
| `pq_scratch` | 0 | 0 | 0 | **unknown** | 0 | needs *both* ML-DSA and ML-KEM — only T4 has the union |
| `worker_stack` | 6,144 | 9,216 | 4,096 | 9,216 | 6,144 | **captured** in each run's `PERFMEM` line |
| `pubkey` | 32 | 32 | 1,312 | 1,952 | 64 | per signature algorithm (ES256 stores x‖y) |

**One value is still genuinely missing: T4's `sizeof(union suit_pq_scratch)`** —
the shared ML-DSA/ML-KEM overlay whose whole purpose is saving RAM on
constrained boards. It is the one field neither the build variables nor the runs
reveal, and T4 is the only tier that has the union at all.

Recovering it needs a T4 build running and a single shell command:

```
> suit_perf
PERFCFG,nrf52840dongle,ml-dsa-65,ml-kem-768,5056,1216,<this number>,9216,1952
```

(The `no update run recorded since boot` line that follows is expected.)

### The notifier retransmits during the fetch

Every log shows extra `suit: received URL:` lines *during* the payload
transfer — 2 in T1 and T3, 1 in T4, plus one arriving after T4's update had
finished. `suit/notify` retransmits its CoAP trigger while the update is
running; the worker is already busy, so they change nothing functionally.

They do add a little host-side traffic to the measured transfer, which makes
them the most likely explanation for **network throughput being the one
device-external control that drifts (1.5 %)** — and it drifts in the right
direction, with T4 (fewest retriggers observed) the slowest at 41.25 kB/s. The
effect is well inside the noise floor and touches no conclusion in §3, but a
future capture could remove it entirely by triggering from the CDC-ACM shell
(`suit fetch coap://…`) instead of `suit/notify`.

### T1

- `size` was not captured from the run. Rebuilt locally with identical flags:
  ```
     text    data     bss     dec     hex
   108236     228   26272  134736   20e50
  ```
  **RAM = 26,500 B `data+bss`**, versus **25,380 B** for the same row 4
  uninstrumented — the instrumentation costs **~1,120 B of RAM** (record table,
  `malloc_monitor`'s allocation array, `ztimer_usec`). Exactly why `SUIT_PERF=1`
  is opt-in, and why these figures must not be quoted against
  [CRYPTO_TIERS.md](CRYPTO_TIERS.md).
- Worker stack was the 6,144 B default (`3 * THREAD_STACKSIZE_LARGE`); no
  override applies to a non-ML-DSA, non-ML-KEM build.

### T2

- `size` not captured; add it if the build log still exists.
- Worker stack 9,216 B, forced by the app Makefile for any `ml-kem-%` build —
  the same forcing as T4, and this tier shows it is needed even with a
  classical signature.
- The `suit_pq_scratch` union is **not** active here: it requires both ML-DSA
  and ML-KEM, so this build carries a standalone `MlKemKey` plus a separate
  1,216 B payload header buffer.

### T3

- `size` not captured; add it if `~/suit-perf-logs/T3-hybrid-pq-sig-build.log`
  still exists.
- The benign `offset does not match` lines are expected: the manifest carries
  both slot components and the device rejects the one that is not its target.

### T4

- `size` not captured; add it if the build log still exists. This is the tier
  where it matters most — row 19's uninstrumented figure is 42,916 B, the
  reference point for "Full PQC uses 16.4 % of the dongle's RAM".
- Worker stack 9,216 B, forced by the app Makefile for any `ml-kem-%` build.
- The `suit_pq_scratch` union is active in this tier only (it requires both
  ML-DSA and ML-KEM), overlaying the idle ML-DSA verify state with the ML-KEM
  decapsulation state and the 1,216 B payload header buffer.

### T5

- `size` **measured locally** on an identically-flagged rebuild:
  ```
     text    data     bss     dec     hex
   112632     228   26600  139460   220c4
  ```
  **RAM 26,828 B** (T1: 26,500 B, **+328 B**) and **ROM 112,632 B**
  (T1: 108,236 B, **+4,396 B**) — wolfCrypt's ECDSA/P-256 code against
  c25519's. The ROM delta is why T5's payload is 4,512 B larger than T1's.
- Keys were generated for this run into `es256-keys/` (`suit/genkey` with
  `SUIT_KEY_ALGO=es256`); `device_x25519.pem` was auto-generated alongside.
- ES256 does **not** switch wolfSSL to the local checkout, so T5 and T1 share
  the pinned `pkg/wolfssl` — which is what makes T5 a valid library control.

---

## 6. Raw data

### T1 — Full Classical

```
PERF,1,ed25519,x25519,total,12923168,0,1,0
PERF,1,ed25519,x25519,mfst_fetch,16247,591,1,1
PERF,1,ed25519,x25519,mfst_cose,113,92,1,1
PERF,1,ed25519,x25519,mfst_kem,882697,0,1,0
PERF,1,ed25519,x25519,mfst_aead,434,499,1,1
PERF,1,ed25519,x25519,parse,11027656,0,1,0
PERF,1,ed25519,x25519,sig_verify,2142548,111,1,1
PERF,1,ed25519,x25519,mfst_digest,643,380,1,1
PERF,1,ed25519,x25519,payload_fetch,8475358,109478,1,1711
PERF,1,ed25519,x25519,payload_kem,2165523,0,1,0
PERF,1,ed25519,x25519,payload_aead,201436,109388,3420,3419
PERF,1,ed25519,x25519,storage_write,3487517,109388,3420,3420
PERF,1,ed25519,x25519,image_digest,320749,218776,2,2
PERF,1,ed25519,x25519,hdr_validate,4,0,1,0
PERFMEM,1,stack_max_used,2756,6144,payload_kem
PERFMEM,1,heap_hwm,0
PERFMEM,1,heap_now,0
```

Full console log:

```
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: started.
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: got manifest with size 591
suit_worker: manifest decrypted (499 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1785778236, highest available: 1785778077
suit: validated sequence number
Formatted component name:
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
validating vendor ID
Comparing 547d0d74-6d3a-5a92-9662-4881afd9407b to 547d0d74-6d3a-5a92-9662-4881afd9407b from manifest
validating vendor ID: OK
validating class id
Comparing 327af4f2-aab7-50cc-97fe-26c85f8944b1 to 327af4f2-aab7-50cc-97fe-26c85f8944b1 from manifest
validating class id: OK
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
SUIT policy check OK.
Formatted component name:
riotboot_flashwrite: initializing update to target slot 1
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: decrypting payload (header 74 bytes)
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: payload decrypted (109388 bytes)
Finalizing payload store
Verifying image digest
Starting digest verification against image
Install correct payload
Verified installed payload
Verifying image digest
Starting digest verification against image
Verified installed payload
Image magic_number: 0x544f4952
Image Version: 0x6a70d03c
Image start address: 0x00071c00
Header chksum: 0x15c6f455

suit_perf: run 1  sig=ed25519 kem=x25519
  phase          |  time [us] |     bytes | calls | chunks
  total          |   12923168 |         0 |     1 |      0
  mfst_fetch     |      16247 |       591 |     1 |      1
  mfst_cose      |        113 |        92 |     1 |      1
  mfst_kem       |     882697 |         0 |     1 |      0
  mfst_aead      |        434 |       499 |     1 |      1
  parse          |   11027656 |         0 |     1 |      0
  sig_verify     |    2142548 |       111 |     1 |      1
  mfst_digest    |        643 |       380 |     1 |      1
  payload_fetch  |    8475358 |    109478 |     1 |   1711
  payload_kem    |    2165523 |         0 |     1 |      0
  payload_aead   |     201436 |    109388 |  3420 |   3419
  storage_write  |    3487517 |    109388 |  3420 |   3420
  image_digest   |     320749 |    218776 |     2 |      2
  hdr_validate   |          4 |         0 |     1 |      0
  stack peak: 2756 of 6144 B (payload_kem), heap peak: 0 B, heap now: 0 B
```

### T2 — Hybrid (PQ-enc)

```
PERF,1,ed25519,ml-kem-768,total,11172258,0,1,0
PERF,1,ed25519,ml-kem-768,mfst_fetch,39713,1643,1,1
PERF,1,ed25519,ml-kem-768,mfst_cose,79,1144,1,1
PERF,1,ed25519,ml-kem-768,mfst_kem,47481,0,1,0
PERF,1,ed25519,ml-kem-768,mfst_aead,439,499,1,1
PERF,1,ed25519,ml-kem-768,parse,10087917,0,1,0
PERF,1,ed25519,ml-kem-768,sig_verify,2267024,111,1,1
PERF,1,ed25519,ml-kem-768,mfst_digest,646,380,1,1
PERF,1,ed25519,ml-kem-768,payload_fetch,7377332,121954,1,1906
PERF,1,ed25519,ml-kem-768,payload_kem,82765,0,1,0
PERF,1,ed25519,ml-kem-768,payload_aead,212033,120812,3778,3777
PERF,1,ed25519,ml-kem-768,storage_write,3781242,120812,3778,3778
PERF,1,ed25519,ml-kem-768,image_digest,354713,241624,2,2
PERF,1,ed25519,ml-kem-768,hdr_validate,4,0,1,0
PERFMEM,1,stack_max_used,8792,9216,payload_kem
PERFMEM,1,heap_hwm,0
PERFMEM,1,heap_now,0
```

Full console log:

```
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: started.
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: got manifest with size 1643
suit_worker: manifest decrypted (499 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1785780474, highest available: 1785780418
suit: validated sequence number
Formatted component name:
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
validating vendor ID
Comparing 547d0d74-6d3a-5a92-9662-4881afd9407b to 547d0d74-6d3a-5a92-9662-4881afd9407b from manifest
validating vendor ID: OK
validating class id
Comparing 327af4f2-aab7-50cc-97fe-26c85f8944b1 to 327af4f2-aab7-50cc-97fe-26c85f8944b1 from manifest
validating class id: OK
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
SUIT policy check OK.
Formatted component name:
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 1126 bytes)
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: payload decrypted (120812 bytes)
Finalizing payload store
Verifying image digest
Starting digest verification against image
Install correct payload
Verified installed payload
Verifying image digest
Starting digest verification against image
Verified installed payload
Image magic_number: 0x544f4952
Image Version: 0x6a70d8fa
Image start address: 0x00071c00
Header chksum: 0x38befd13

suit_perf: run 1  sig=ed25519 kem=ml-kem-768
  phase          |  time [us] |     bytes | calls | chunks
  total          |   11172258 |         0 |     1 |      0
  mfst_fetch     |      39713 |      1643 |     1 |      1
  mfst_cose      |         79 |      1144 |     1 |      1
  mfst_kem       |      47481 |         0 |     1 |      0
  mfst_aead      |        439 |       499 |     1 |      1
  parse          |   10087917 |         0 |     1 |      0
  sig_verify     |    2267024 |       111 |     1 |      1
  mfst_digest    |        646 |       380 |     1 |      1
  payload_fetch  |    7377332 |    121954 |     1 |   1906
  payload_kem    |      82765 |         0 |     1 |      0
  payload_aead   |     212033 |    120812 |  3778 |   3777
  storage_write  |    3781242 |    120812 |  3778 |   3778
  image_digest   |     354713 |    241624 |     2 |      2
  hdr_validate   |          4 |         0 |     1 |      0
  stack peak: 8792 of 9216 B (payload_kem), heap peak: 0 B, heap now: 0 B
suit_worker: update successful
```

Note `manifest decrypted (499 bytes)` — byte-identical to T1's plaintext, from
a 1,643 B wire manifest instead of 591 B. The 1,052 B difference is the
ML-KEM-768 recipient structure, and it is the only thing that differs between
these two tiers' manifests.

### T3 — Hybrid (PQ-sig)

```
PERF,1,ml-dsa-44,x25519,total,11588146,0,1,0
PERF,1,ml-dsa-44,x25519,mfst_fetch,67262,2951,1,1
PERF,1,ml-dsa-44,x25519,mfst_cose,114,92,1,1
PERF,1,ml-dsa-44,x25519,mfst_kem,882065,0,1,0
PERF,1,ml-dsa-44,x25519,mfst_aead,2078,2859,1,1
PERF,1,ml-dsa-44,x25519,parse,9641071,0,1,0
PERF,1,ml-dsa-44,x25519,sig_verify,42146,2469,1,1
PERF,1,ml-dsa-44,x25519,mfst_digest,644,380,1,1
PERF,1,ml-dsa-44,x25519,payload_fetch,9153549,121514,1,1899
PERF,1,ml-dsa-44,x25519,payload_kem,2164235,0,1,0
PERF,1,ml-dsa-44,x25519,payload_aead,213573,121424,3796,3795
PERF,1,ml-dsa-44,x25519,storage_write,3874412,121424,3796,3796
PERF,1,ml-dsa-44,x25519,image_digest,356532,242848,2,2
PERF,1,ml-dsa-44,x25519,hdr_validate,4,0,1,0
PERFMEM,1,stack_max_used,3372,4096,payload_kem
PERFMEM,1,heap_hwm,0
PERFMEM,1,heap_now,0
```

Full console log:

```
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: started.
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: got manifest with size 2951
suit_worker: manifest decrypted (2859 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1785778956, highest available: 1785778894
suit: validated sequence number
Formatted component name:
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
validating vendor ID
Comparing 547d0d74-6d3a-5a92-9662-4881afd9407b to 547d0d74-6d3a-5a92-9662-4881afd9407b from manifest
validating vendor ID: OK
validating class id
Comparing 327af4f2-aab7-50cc-97fe-26c85f8944b1 to 327af4f2-aab7-50cc-97fe-26c85f8944b1 from manifest
validating class id: OK
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
SUIT policy check OK.
Formatted component name:
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 74 bytes)
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: payload decrypted (121424 bytes)
Finalizing payload store
Verifying image digest
Starting digest verification against image
Install correct payload
Verified installed payload
Verifying image digest
Starting digest verification against image
Verified installed payload
Image magic_number: 0x544f4952
Image Version: 0x6a70d30c
Image start address: 0x00071c00
Header chksum: 0x2106f725

suit_perf: run 1  sig=ml-dsa-44 kem=x25519
  phase          |  time [us] |     bytes | calls | chunks
  total          |   11588146 |         0 |     1 |      0
  mfst_fetch     |      67262 |      2951 |     1 |      1
  mfst_cose      |        114 |        92 |     1 |      1
  mfst_kem       |     882065 |         0 |     1 |      0
  mfst_aead      |       2078 |      2859 |     1 |      1
  parse          |    9641071 |         0 |     1 |      0
  sig_verify     |      42146 |      2469 |     1 |      1
  mfst_digest    |        644 |       380 |     1 |      1
  payload_fetch  |    9153549 |    121514 |     1 |   1899
  payload_kem    |    2164235 |         0 |     1 |      0
  payload_aead   |     213573 |    121424 |  3796 |   3795
  storage_write  |    3874412 |    121424 |  3796 |   3796
  image_digest   |     356532 |    242848 |     2 |      2
  hdr_validate   |          4 |         0 |     1 |      0
  stack peak: 3372 of 4096 B (payload_kem), heap peak: 0 B, heap now: 0 B
suit_worker: update successful
suit_worker: rebooting...
```

### T4 — Full PQC

```
PERF,1,ml-dsa-65,ml-kem-768,total,9087431,0,1,0
PERF,1,ml-dsa-65,ml-kem-768,mfst_fetch,106051,4892,1,1
PERF,1,ml-dsa-65,ml-kem-768,mfst_cose,80,1144,1,1
PERF,1,ml-dsa-65,ml-kem-768,mfst_kem,48124,0,1,0
PERF,1,ml-dsa-65,ml-kem-768,mfst_aead,2873,3748,1,1
PERF,1,ml-dsa-65,ml-kem-768,parse,7934211,0,1,0
PERF,1,ml-dsa-65,ml-kem-768,sig_verify,72474,3358,1,1
PERF,1,ml-dsa-65,ml-kem-768,mfst_digest,645,380,1,1
PERF,1,ml-dsa-65,ml-kem-768,payload_fetch,7403141,127246,1,1989
PERF,1,ml-dsa-65,ml-kem-768,payload_kem,82737,0,1,0
PERF,1,ml-dsa-65,ml-kem-768,payload_aead,222808,126104,3943,3942
PERF,1,ml-dsa-65,ml-kem-768,storage_write,4012516,126104,3943,3943
PERF,1,ml-dsa-65,ml-kem-768,image_digest,369683,252208,2,2
PERF,1,ml-dsa-65,ml-kem-768,hdr_validate,4,0,1,0
PERFMEM,1,stack_max_used,8792,9216,payload_kem
PERFMEM,1,heap_hwm,0
PERFMEM,1,heap_now,0
```

Full console log:

```
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: started.
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: got manifest with size 4892
suit_worker: manifest decrypted (3748 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1785779540, highest available: 1785779480
suit: validated sequence number
Formatted component name:
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
validating vendor ID
Comparing 547d0d74-6d3a-5a92-9662-4881afd9407b to 547d0d74-6d3a-5a92-9662-4881afd9407b from manifest
validating vendor ID: OK
validating class id
Comparing 327af4f2-aab7-50cc-97fe-26c85f8944b1 to 327af4f2-aab7-50cc-97fe-26c85f8944b1 from manifest
validating class id: OK
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
SUIT policy check OK.
Formatted component name:
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 1126 bytes)
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: payload decrypted (126104 bytes)
Finalizing payload store
Verifying image digest
Starting digest verification against image
Install correct payload
Verified installed payload
Verifying image digest
Starting digest verification against image
Verified installed payload
Image magic_number: 0x544f4952
Image Version: 0x6a70d554
Image start address: 0x00071c00
Header chksum: 0x2a26f96d

suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_perf: run 1  sig=ml-dsa-65 kem=ml-kem-768
  phase          |  time [us] |     bytes | calls | chunks
  total          |    9087431 |         0 |     1 |      0
  mfst_fetch     |     106051 |      4892 |     1 |      1
  mfst_cose      |         80 |      1144 |     1 |      1
  mfst_kem       |      48124 |         0 |     1 |      0
  mfst_aead      |       2873 |      3748 |     1 |      1
  parse          |    7934211 |         0 |     1 |      0
  sig_verify     |      72474 |      3358 |     1 |      1
  mfst_digest    |        645 |       380 |     1 |      1
  payload_fetch  |    7403141 |    127246 |     1 |   1989
  payload_kem    |      82737 |         0 |     1 |      0
  payload_aead   |     222808 |    126104 |  3943 |   3942
  storage_write  |    4012516 |    126104 |  3943 |   3943
  image_digest   |     369683 |    252208 |     2 |      2
  hdr_validate   |          4 |         0 |     1 |      0
  stack peak: 8792 of 9216 B (payload_kem), heap peak: 0 B, heap now: 0 B
```

Wire-format confirmations visible in this log alone: the 1,144 B ML-KEM-768
recipient overhead (4,892 → 3,748 B) and the 1,126 B ML-KEM-768 payload header,
both matching [CRYPTO_TIERS.md §2](CRYPTO_TIERS.md) exactly.

### T5 — Classical, ECDSA (ES256)

```
PERF,1,es256,x25519,total,11994368,0,1,0
PERF,1,es256,x25519,mfst_fetch,16061,591,1,1
PERF,1,es256,x25519,mfst_cose,114,92,1,1
PERF,1,es256,x25519,mfst_kem,882008,0,1,0
PERF,1,es256,x25519,mfst_aead,431,499,1,1
PERF,1,es256,x25519,parse,10099293,0,1,0
PERF,1,es256,x25519,sig_verify,450804,111,1,1
PERF,1,es256,x25519,mfst_digest,643,380,1,1
PERF,1,es256,x25519,payload_fetch,9225487,113990,1,1782
PERF,1,es256,x25519,payload_kem,2164148,0,1,0
PERF,1,es256,x25519,payload_aead,199841,113900,3561,3560
PERF,1,es256,x25519,storage_write,3620649,113900,3561,3561
PERF,1,es256,x25519,image_digest,334121,227800,2,2
PERF,1,es256,x25519,hdr_validate,4,0,1,0
PERFMEM,1,stack_max_used,3480,6144,sig_verify
PERFMEM,1,heap_hwm,0
PERFMEM,1,heap_now,0
```

Full console log:

```
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: started.
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit_worker: got manifest with size 591
suit_worker: manifest decrypted (499 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1785781555, highest available: 1785781494
suit: validated sequence number
Formatted component name:
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
validating vendor ID
Comparing 547d0d74-6d3a-5a92-9662-4881afd9407b to 547d0d74-6d3a-5a92-9662-4881afd9407b from manifest
validating vendor ID: OK
validating class id
Comparing 327af4f2-aab7-50cc-97fe-26c85f8944b1 to 327af4f2-aab7-50cc-97fe-26c85f8944b1 from manifest
validating class id: OK
Comparing manifest offset 4000 with other slot offset
offset does not match
Comparing manifest offset 71800 with other slot offset
SUIT policy check OK.
Formatted component name:
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 74 bytes)
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: received URL: "coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc"
suit: payload decrypted (113900 bytes)
Finalizing payload store
Verifying image digest
Starting digest verification against image
Install correct payload
Verified installed payload
Verifying image digest
Starting digest verification against image
Verified installed payload
Image magic_number: 0x544f4952
Image Version: 0x6a70dd33
Image start address: 0x00071c00
Header chksum: 0x49a2014d

suit_perf: run 1  sig=es256 kem=x25519
  phase          |  time [us] |     bytes | calls | chunks
  total          |   11994368 |         0 |     1 |      0
  mfst_fetch     |      16061 |       591 |     1 |      1
  mfst_cose      |        114 |        92 |     1 |      1
  mfst_kem       |     882008 |         0 |     1 |      0
  mfst_aead      |        431 |       499 |     1 |      1
  parse          |   10099293 |         0 |     1 |      0
  sig_verify     |     450804 |       111 |     1 |      1
  mfst_digest    |        643 |       380 |     1 |      1
  payload_fetch  |    9225487 |    113990 |     1 |   1782
  payload_kem    |    2164148 |         0 |     1 |      0
  payload_aead   |     199841 |    113900 |  3561 |   3560
  storage_write  |    3620649 |    113900 |  3561 |   3561
  image_digest   |     334121 |    227800 |     2 |      2
  hdr_validate   |          4 |         0 |     1 |      0
  stack peak: 3480 of 6144 B (sig_verify), heap peak: 0 B, heap now: 0 B
suit_worker: update successful
```

Note `got manifest with size 591` and `manifest decrypted (499 bytes)` —
byte-identical to T1, with the same 111 B COSE object. ES256 and Ed25519
signatures are both 64 B, so **T5 and T1 differ in no wire quantity at all**;
only the verifying library differs. That is what makes §3.1's factorisation
sound.
