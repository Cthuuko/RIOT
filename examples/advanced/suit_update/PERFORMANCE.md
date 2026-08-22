# Performance & memory instrumentation

Every other document in this project measures **space**:
[CRYPTO_TIERS.md](CRYPTO_TIERS.md) and the four `DEVICE_*.md` matrices report
link-time RAM/ROM and wire-format sizes. This document covers the other axis —
**time, throughput, and runtime memory** — via opt-in checkpoints compiled into
the device firmware with `SUIT_PERF=1`.

It exists because two of the project's central claims are runtime facts that
link-time analysis provably cannot produce:

- wolfCrypt's ML-KEM working set (~8.5–9 KB of *stack* after the
  `WOLFSSL_NO_MALLOC` fix — previously an invisible heap allocation that made
  every pre-2026-07-20 feasibility number wrong, see
  [FINDINGS.md §4](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery));
- the cost of ML-DSA verification on a 48 MHz M0+, until now recorded only as
  the qualitative "noticeable pause".

> **This document describes the instrument, not the results.** Captured
> measurements belong in [FINDINGS.md](FINDINGS.md); the tier-by-tier reading of
> them belongs in [CRYPTO_TIERS.md](CRYPTO_TIERS.md).

---

## 1. Enabling it

```bash
SUIT_PERF=1 BOARD=<board> make -C examples/advanced/suit_update all
```

Off by default, and deliberately so: `SUIT_PERF=1` adds `ztimer_usec`,
`malloc_monitor`, the per-phase record table and the report's `printf`s.

> ⚠️ **An instrumented build is not a matrix build.** Never quote its `size`
> output against [CRYPTO_TIERS.md](CRYPTO_TIERS.md) or a `DEVICE_*.md` matrix —
> it carries a constant +1,120 B of RAM (§5). With `SUIT_PERF` unset the
> firmware is byte-identical to one built without this feature — verified on
> samr21-xpro: 21,552 B `data+bss`, exactly matching matrix row 4.

The flag also sets `-DSCHED_TEST_STACK=1`, which makes `thread_create()` fill
the worker stack with the canary that the high-water measurement reads. Without
it the stack figure would be silently meaningless rather than absent, so the
application forces it rather than documenting it as a prerequisite.

---

## 2. What is measured — six operation classes

The update is instrumented by *class of operation*, not by source file. Only
the first two classes vary with the crypto tier; the rest are identical in
every tier and act as the control. **If the invariant classes drift between two
runs, the comparison is not trustworthy** — that is what they are for.

### A. Secret-value generation — tier-sensitive

Turning the embedded long-term key into the per-update content-encryption key.
This is the entire key-establishment axis.

| Step | Classical (`x25519`) | Post-quantum (`ml-kem-768/1024`) |
|---|---|---|
| Key reconstitution | RFC 7748 clamping of the 32 B scalar | deterministic re-expansion of the 64 B FIPS 203 `d‖z` seed into a full keypair |
| Shared secret | X25519 scalar multiplication | ML-KEM decapsulation — internally re-runs encapsulation for the implicit-rejection check, which dominates |
| Key derivation | HKDF-SHA256 over the COSE_KDF_Context | identical |

Phases: `mfst_kem`, `payload_kem`. **This happens twice per update** — the
manifest and the payload each carry their own recipient structure.

### B. Signature verification — tier-sensitive

One COSE verification per update: Ed25519, ES256/384/512, or ML-DSA-44/65/87.
Phase: `sig_verify`, with `bytes` = the COSE_Sign1 object as received.

### C. Symmetric decryption — tier-invariant

ChaCha20-Poly1305, identical in all four tiers, in two shapes: one-shot
in-place over the manifest (`mfst_aead`), and streaming in-place over the
payload (`payload_aead` — one AEAD update per transport block, plus a final
interval for the tag check, hence `calls == chunks + 1`).

Any difference here across tiers is payload size, never crypto. This is the
control variable that makes the class A/B deltas readable.

### D. Integrity hashing — tier-invariant

`mfst_digest` (SHA-256 binding the manifest to the COSE payload) and
`image_digest` (streaming read-back over the stored image). On flash-backed
storage the latter re-reads the whole slot, so it is as much a storage-read
benchmark as a hashing one — and it runs **twice** per update (once after the
fetch, once after install), which the `calls` column shows.

### E. Transport — tier-invariant CPU, tier-sensitive volume

`mfst_fetch` and `payload_fetch`: wall time, bytes as they arrive on the wire
(ciphertext plus COSE header in an encrypted build, the plain object
otherwise), and `chunks` = transport blocks. The manifest grows substantially
with the tier, so this class separates the **wire cost** of going
post-quantum from its **CPU cost**.

### F. Storage — tier-invariant

`storage_write`: writes through the storage backend plus the finalize step.

### Envelope

`total` (trigger → done) and `parse` (which contains B, D, E, F and the
payload half of A and C), so each class can be expressed as a share of update
latency.

---

## 3. Memory indicators

| Indicator | Question it answers |
|---|---|
| **Worker stack high-water** | How close does this tier come to overflowing `SUIT_WORKER_STACKSIZE`? Sampled after each tier-sensitive phase. |
| **Heap high-water** | Does `WOLFSSL_NO_MALLOC` hold at runtime — is ML-KEM really allocation-free? |
| **Static config dump** | Which tier and buffer sizing produced this log? Printed once at boot. |
| **Heap in use at end of run** | Did the update leak, or hand memory back? Reported as `heap_now` alongside the peak. |
| **Free RAM after the run** | Whole-system sanity figure via `heap_stats()` — **printed only where the libc allows it**. It reaches into newlib's `mallinfo()`, whose object also references `fiprintf`; on boards that replace newlib's stdio with `mpaland-printf` (RIOT's default on small newlib targets, samr21-xpro included) that symbol is deliberately wrapped to an undefined one, so the call is a link error there rather than a cost. The `malloc_monitor` figures above are unconditional and answer the same question. |

**Read the stack number correctly.** The canary gives a *monotonic* high-water
mark for the whole thread, so a sample cannot isolate one phase's own usage —
the phase named in the report is the one that **raised** the mark to its final
value. That is exactly the question of interest ("which operation drives the
stack requirement"), but it is not a per-phase stack profile.

---

## 4. Output format

Boot, once:

```
suit_perf: native64 sig=ed25519 kem=x25519 pubkey=32 B
suit_perf: manifest_buf=768 B fw_hdr=192 B pq_scratch=0 B worker_stack=98304 B
PERFCFG,native64,ed25519,x25519,768,192,0,98304,32
```

After each update, a table plus its machine-readable mirror:

```
suit_perf: run 1  sig=ed25519 kem=x25519
  phase          |  time [us] |     bytes | calls | chunks
  total          |      11815 |         0 |     1 |      0
  ...
  stack peak: 5288 of 98304 B (total), heap peak: 16384 B, heap now: 16384 B
PERF,1,ed25519,x25519,mfst_kem,2465,0,1,0
PERFMEM,1,stack_max_used,5288,98304,total
PERFMEM,1,heap_hwm,16384
PERFMEM,1,heap_now,16384
```

Schemas:

| Prefix | Fields |
|---|---|
| `PERFCFG` | `board, sig_algo, kem_algo, manifest_bufsize, fw_hdr_len, pq_scratch_len, worker_stacksize, pubkey_len` |
| `PERF` | `run, sig_algo, kem_algo, phase, time_us, bytes, calls, chunks` |
| `PERFMEM` | `run, metric, value[, of, peak_phase]` |

`calls` counts timed intervals, `chunks` counts byte-accounting events — they
differ where a phase is timed once but counted per block (`payload_fetch`) or
timed once more than it is counted (`payload_aead`'s tag check).

**Every phase is emitted on every run, even unused ones**, so the CSV stays
rectangular across tiers: `grep '^PERF' term.log` is the complete dataset.
`> suit_perf` in the shell re-prints the last run's report.

---

## 5. Capture procedure

1. Build with `SUIT_PERF=1 PROGRESS_BAR=0` — the progress bar reprints on
   every received block and would otherwise dominate the transport and
   storage classes.
2. Run the update exactly as in the relevant `DEVICE_*.md`, capturing the
   terminal to a log.
3. `grep '^PERF' term.log > tier-<name>.csv`.
4. Repeat per tier, holding **payload size, `APP_VER` and network path
   constant**.

### Cost of the instrumentation, and where it still fits

Measured 2026-08-03 by rebuilding the four headline tiers of
[CRYPTO_TIERS.md §1](CRYPTO_TIERS.md) with `SUIT_PERF=1`. The overhead is a
**constant +1,120 B RAM and ~+200 B ROM** in every tier on both boards — it is
`malloc_monitor`'s allocation table plus the record table, neither of which
scales with the crypto:

| Tier | samr21 `data+bss` (of 32,768 B) | dongle `data+bss` (of 262,144 B) |
|---|---|---|
| Full Classical | 22,672 B — 9.9 KB spare | 26,500 B |
| Hybrid (PQ-enc) | 31,856 B — **912 B spare** | 35,684 B |
| Hybrid (PQ-sig) | 32,400 B — **368 B spare** | 36,228 B |
| Full PQC | ❌ overflow 7,444 B | 44,036 B |

Each figure is exactly its `CRYPTO_TIERS.md` baseline + 1,120 B, including the
Full PQC overflow (6,324 → 7,444 B). So **all three tiers that are feasible on
the samr21 remain feasible instrumented**, if barely. What does *not* survive
are the near-zero-margin sub-tier rows — samr21 matrix row 7 (ML-KEM-1024, 48 B
spare) and row 11 (ML-DSA-65 + X25519, 88 B spare) — which the +1,120 B pushes
into overflow.

Which board:

- **A four-tier comparison is only possible on the nRF52840 Dongle.** Full PQC
  does not fit on the samr21 in any configuration, instrumented or not.
- **samr21-xpro** contributes Full Classical and the two hybrids on a much
  slower M0+ — the more interesting datapoint for `sig_verify`.
- **native64** is for validating the instrument, not for timing: its stack is
  unbounded and its CPU unrepresentative. Its X25519 also runs the software
  `c25519` pkg, which on x86-64 is *slower* than wolfCrypt's ML-KEM — an
  inversion that does not survive onto Cortex-M and is a good illustration of
  why native timings must not be quoted.

### Derived metrics

| Metric | From |
|---|---|
| Crypto CPU vs network | (A+B+C+D) against E |
| PQ overhead, KEM axis | `mfst_kem + payload_kem` minus the Full Classical run |
| PQ overhead, signature axis | `sig_verify` minus the Full Classical run |
| Throughput | `bytes / time_us` for `payload_aead`, `image_digest`, `storage_write` |
| Stack headroom | `worker_stacksize − stack_max_used` |
| Energy proxy | `total` × datasheet active current — **an estimate, not a measurement** |

The two hybrid tiers isolate one axis each, so the two overhead figures are
separable by construction rather than by assumption.

---

## 6. Limits of the measurement

- `ztimer_usec` is 32-bit: a single phase longer than ~71 minutes is not
  representable. Resolution is one timer tick, so the sub-10 µs phases
  (`mfst_cose`, `mfst_digest`) carry meaningful quantisation error and should
  not be compared between tiers.
- `payload_aead` and `storage_write` take two `ztimer_now()` readings per
  transport block. The overhead is small and, being tier-independent, cancels
  in tier deltas — but it is inside the `total` figure.
- Timing is wall clock on a running RIOT: interrupts and the network stack are
  included. The crypto phases are pure computation and unaffected in practice;
  the transport phases are wall time by definition.
- The report prints from the worker thread before the completion callback (which
  reboots on success for flash-write boards). On a board with a slow console
  the report itself is part of the last phase's wall time, not of `total`.

---

## 7. Implementation

| File | Role |
|---|---|
| `sys/include/suit/perf.h` | phase enum + API; collapses to `((void)0)` when the module is off |
| `sys/suit/perf.c` | record table, stack/heap accounting, report printer |
| `sys/suit/transport/worker.c` | `total`, `mfst_fetch`, `parse`, `hdr_validate`; owns the worker stack and hands it over |
| `sys/suit/encrypt/decrypt.c` | `mfst_cose`, `mfst_kem`, `mfst_aead` |
| `sys/suit/encrypt/payload_decrypt.c` | `payload_kem`, `payload_aead` |
| `sys/suit/handlers_envelope.c` | `sig_verify`, `mfst_digest` |
| `sys/suit/handlers_command_seq.c` | `payload_fetch`, `storage_write`, `image_digest` |
| `sys/suit/Makefile.dep` | pulls in `ztimer_usec` + `malloc_monitor` |
| `examples/advanced/suit_update/Makefile` | the `SUIT_PERF` knob and `-DSCHED_TEST_STACK=1` |

---

## 8. Host-side instrumentation

Everything above measures the **consumer**: verification, decapsulation,
decryption on the device. `SUIT_HOST_PERF=1` measures the **producer** — the
build host that hashes, **signs**, **encapsulates** and **encrypts**. Results:
[PERF_RESULTS_HOST.md](PERF_RESULTS_HOST.md).

### 8.1 Enabling it

```bash
SUIT_HOST_PERF=1 [SUIT_HOST_PERF_LOG=<path>] make -C examples/advanced/suit_update suit/publish
```

Same opt-in contract as `SUIT_PERF=1`: with the variable unset every checkpoint
is a no-op, no line is written, and every tool's output is byte-identical to
one from the uninstrumented sources (verified by `cmp` against the pre-change
`suit-tool sign`). Lines go to **stderr**, so tool stdout and pipes stay clean.

One update is one device process, but **one publish is five host processes** —
`suit-tool create`, `suit-tool sign`, `encrypt_firmware.py` twice (one per
slot) and `encrypt_manifest.py`. `SUIT_HOST_PERF_LOG` **appends**, so all five
land in one per-tier CSV. The device schema's `run` field is therefore replaced
by a `tool` field.

### 8.2 Phases, and what they pair with

| Host phase | Device counterpart | Operation |
|---|---|---|
| `mfst_create` | — | `suit-tool create`: compile + CBOR-encode the unsigned manifest |
| `key_load` | — | PEM parse; for ML-DSA this expands the 32 B FIPS 204 seed into the signing key |
| `mfst_digest` | `mfst_digest` | the SHA-256 binding the COSE payload to the manifest body |
| **`sig_sign`** | **`sig_verify`** | the COSE signature — over the same ~55 B `Sig_structure` for every algorithm |
| `mfst_serialize` | — | CBOR-encode the signed envelope; `bytes` = the manifest plaintext |
| **`mfst_kem`** | **`mfst_kem`** | producer key establishment: X25519 ephemeral keygen + ECDH, or ML-KEM encapsulation, plus HKDF |
| `mfst_aead` | `mfst_aead` | one-shot ChaCha20-Poly1305 over the manifest |
| `mfst_cose` | `mfst_cose` | `bytes` = COSE_Encrypt container overhead (92 B X25519, 1,144 B ML-KEM-768) |
| **`payload_kem`** | **`payload_kem`** | same as `mfst_kem`, per slot binary — so **twice per publish** |
| `payload_aead` | `payload_aead` | one shot over the whole image on the host, ~3,400 streamed chunks on the device |
| `payload_hdr` | — | `bytes` = detached-COSE header (74 B X25519, 1,126 B ML-KEM-768) |
| `total` | `total` | per process, from module import to exit — includes argument parsing and file I/O |

Two scope asymmetries are inherent to the producer/consumer roles, not to the
measurement, and **both bias against the host**: host `mfst_kem` for X25519
includes ephemeral key generation, which the device never does; host ML-KEM
`mfst_kem` is encapsulation only, while the device's decapsulation internally
re-runs encapsulation for the FIPS 203 implicit-rejection check.

### 8.3 Output format

```
HOSTCFG,suit-tool-sign,ml-dsa-65,none,3.10.12,48.0.0,13th Gen Intel(R) Core(TM) i5-13600K
HOSTPERF,suit-tool-sign,ml-dsa-65,none,sig_sign,815,56,1,1
```

| Prefix | Fields |
|---|---|
| `HOSTCFG` | `tool, sig_algo, kem_algo, python_ver, cryptography_ver, cpu` |
| `HOSTPERF` | `tool, sig_algo, kem_algo, phase, time_us, bytes, calls, chunks` |

Deliberately the same shape as `PERFCFG`/`PERF`, so `grep '^HOSTPERF' tier.csv`
is the complete dataset exactly as `grep '^PERF' term.log` is on the device.
Times are `time.perf_counter_ns()` reported in **µs**, matching the device's
`time [us]` column.

### 8.4 One sample is not enough here — use the harness

The device's *compute* phases reproduce to well under 0.1 % on a bare-metal
Cortex-M4 — `sig_verify` varies by 0.008 % over 50 runs — but that does not
make one run per tier sound there either: the device-side repeats found a
rare stack excursion, a bimodal flash-write cost and a real 147 ms
Ed25519 difference that a single capture reported as noise
([PERF_RESULTS_NRF52840.md §3.12–3.14](PERF_RESULTS_NRF52840.md)). Use
`device_perf_bench.py` there too (§9). It is **not** sound on a 20-thread desktop under WSL2, and
ML-DSA makes it worse: FIPS 204 signing is a *rejection-sampling loop* whose
iteration count depends on the data, so its latency is a distribution spanning
~20× ([PERF_RESULTS_HOST.md §3.3](PERF_RESULTS_HOST.md)).

```bash
python3 dist/tools/suit/host_crypto_bench.py --repeats 200 --aead-repeats 50
```

`dist/tools/suit/host_crypto_bench.py` re-runs each primitive over **fixed
reference bytes, identical across all five tiers**, discards warmups, and emits
median / min / p95 / stdev as `HOSTBENCH,` lines. It needs no build, no
hardware and no network, and finishes in about a minute.

**Use the harness for every algorithm comparison; use the instrumented tools to
answer "what does a publish cost".** The two disagree by up to 50× on the
X25519 phases, because the first asymmetric operation in a fresh Python process
pays ~2.0 ms of one-time OpenSSL initialisation against a 22 µs steady-state
operation — an artifact of the Python tooling that the harness's warmup removes
and a single-shot publish cannot.

### 8.5 Implementation

| File | Role |
|---|---|
| `dist/tools/suit/hostperf.py` | record table, `phase()`/`count()` API, atexit reporter; no-op when `SUIT_HOST_PERF` is unset |
| `dist/tools/suit/host_crypto_bench.py` | the repeat harness |
| `dist/tools/suit/suit-manifest-generator/suit_tool/hostperf.py` | package-local shim (suit-tool is separately installable), degrades to a no-op stub |
| `.../suit_tool/create.py` | `mfst_create` |
| `.../suit_tool/sign.py` | `key_load`, `mfst_digest`, `sig_sign`, `mfst_serialize` |
| `manifest-encryption/encrypt_manifest.py` | `mfst_kem`, `mfst_aead`, `mfst_cose` (X25519) |
| `manifest-encryption-mlkem/encrypt_manifest.py` | the same, for ML-KEM |
| `firmware-encryption/encrypt_firmware.py` | `payload_kem`, `payload_aead`, `payload_hdr` |

The three `encrypt_*.py` scripts import the module through a `__file__`-relative
path inside a `try/except ImportError`, so they keep working as the standalone
interop examples their READMEs document.

---

## 9. Device-side automation

§8's harness gets its strength from repeats. The device side had none: the
capture in [PERF_RESULTS_NRF52840.md](PERF_RESULTS_NRF52840.md) is **one run
per tier**, hand-driven through [the runbook](PERF_RUNBOOK_NRF52840.md)'s eight
steps, because a successful update reboots the dongle through a USB
re-enumeration that kills picocom mid-capture.

`dist/tools/suit/device_perf_bench.py` removes that limit. It runs N updates
back to back on an **already-flashed** tier and reopens the console itself
after every reboot — the one thing the manual procedure could not do.

```bash
python3 dist/tools/suit/device_perf_bench.py --tier T4 --repeats 20
```

### 9.1 What one iteration does

| Step | Notes |
|---|---|
| Publish at a fresh `APP_VER` | `RIOTBOOT_SKIP_COMPILE=1`; the riotboot header is still regenerated (`makefiles/boot/riotboot.mk` marks `%.hdr` `FORCE`), so a repeat costs ~1 s instead of a rebuild |
| Encrypt the manifest | the tier's own `encrypt_manifest.py` |
| Trigger | `suit fetch <url>` **on the device shell** by default — this is the fix [PERF_RESULTS_NRF52840.md §5](PERF_RESULTS_NRF52840.md) proposes for the notifier retransmitting during the payload transfer. `--trigger notify` keeps the original method as a control |
| Scrape | reads until all 14 `PERF,` phases and all five `PERFMEM,` values have arrived; an incomplete report is an error, never a short average |
| Reconnect | waits for the tty to cycle and reopens it |
| `suit_perf` | recovers the `PERFCFG` line **on every run** — the manual capture could never get it, which is why T4's `pq_scratch` was missing |
| `current_slot` | records which slot this run installed to; updates alternate 0/1 |

Adding `--host-perf-log <path>` also turns on §8's producer instrumentation, so
each iteration yields a **paired host+device sample** at no extra cost.

### 9.2 Output

Per-run raw lines go to `~/suit-perf-logs/dev-<tier>-<trigger>.csv` in exactly
the form `grep -E '^(PERFCFG|PERF|PERFMEM),'` produced by hand, so existing
analysis still applies. The summary is `DEVBENCH,` lines:

```
DEVBENCH,tier,sig_algo,kem_algo,phase,n,median_us,min_us,p95_us,stdev_us,bytes
```

Identical in shape to §8's `HOSTBENCH,`, and produced by the same
`dist/tools/suit/perfstats.py` — so a spread quoted for the host and one quoted
for the device mean the same thing rather than being coincidentally similar.
Percentiles are nearest-rank, not interpolated: at N = 20 an interpolated p95
would invent a value between two real updates.

### 9.3 What repeats buy here

Less than on the host — a bare-metal Cortex-M4 running one thread is far more
reproducible than a 20-thread desktop — but four specific things the
single-sample capture left open:

1. **A measured noise floor.** ~~[§4](PERF_RESULTS_NRF52840.md) extrapolates
   "~6 % for CPU-bound phases" from *one* pair of Ed25519 measurements.~~
   **Resolved 2026-08-10**: within a build the compute phases reproduce to
   0.008 %; the 6 % is a real between-build difference (2,142.8 vs 2,290.0 ms),
   not noise.
2. **T1's `payload_aead` outlier** (543 vs 566–570 kB/s), which §4's correction
   box leaves with "a reason that is not the wolfSSL tree".
3. **T4's `sizeof(union suit_pq_scratch)`** — §5's "one value still genuinely
   missing", now captured automatically every run.
4. **A falsifiable prediction from the host side.**
   [PERF_RESULTS_HOST.md §3.3](PERF_RESULTS_HOST.md) found ML-DSA *signing*
   spans ~20× because FIPS 204 signing is a rejection-sampling loop, and
   asserts *verification* is straight-line and should show no such spread.
   Repeated `sig_verify` measurements test that directly; a null result is a
   real finding.

### 9.4 What still needs `sudo` — and what does not

**The measurement loop is entirely unprivileged.** Two one-time steps are not,
and the harness's pre-flight checks for both and prints the exact command
rather than running it:

- **Flashing a tier** (`DFU="sudo dfu-util"`), per [the runbook](PERF_RUNBOOK_NRF52840.md) step 3.
- **The host's CDC-ECM address.** The interface is destroyed and recreated on
  every re-enumeration, so `2001:db8::1/64` is lost on each reboot. Run
  `sudo dist/tools/suit/suit-ecm-keeper.sh` once per session in its own shell;
  it re-adds the address whenever the interface reappears, keeping the loop
  sudo-free.

`usbipd attach` no longer needs administrator privileges (usbipd-win ≥ 4.0), so
`usbipd.exe attach --wsl --auto-attach --busid <BUSID>` can be run **from WSL**
and survives the re-enumerations by itself — replacing the runbook's
"re-attach from admin PowerShell" step at every reboot.

### 9.5 Implementation

| File | Role |
|---|---|
| `dist/tools/suit/device_perf_bench.py` | the harness: tier table, console with reopen-after-reboot, report scraping, aggregation |
| `dist/tools/suit/perfstats.py` | `summarise()` + the `HOSTBENCH`/`DEVBENCH` line format, shared with §8's harness |
| `dist/tools/suit/suit-ecm-keeper.sh` | user-run, sudo, once per session — keeps the CDC-ECM address alive |
| `examples/advanced/suit_update/main.c` | the `suit_perf` / `current_slot` shell commands it drives |
| `dist/pythonlibs/testrunner`, `tests-with-config/01-run.py` | the prior art the loop structure follows |

Run `--repeats 0` for a dry run: it exercises pre-flight, publish and manifest
encryption with **no board attached**, which is how the harness is checked
before a capture session.

---

*Related: [PERF_RESULTS_NRF52840.md](PERF_RESULTS_NRF52840.md) (device results) ·
[PERF_RESULTS_HOST.md](PERF_RESULTS_HOST.md) (host results) ·
[CRYPTO_TIERS.md](CRYPTO_TIERS.md) (what the tiers are) ·
[FINDINGS.md](FINDINGS.md) (measurements) ·
[CRYPTO_OPERATIONS.md](CRYPTO_OPERATIONS.md) (what each operation does) ·
[SETUP_COMMON.md](SETUP_COMMON.md) (the update cycle).*
