# Perf capture runbook — nRF52840 Dongle, five crypto configurations

Step-by-step commands to run an instrumented (`SUIT_PERF=1`) SUIT update on
real **nRF52840 Dongle** hardware, once per crypto tier, and collect the
measurements.

- **What is measured and why**: [PERFORMANCE.md](PERFORMANCE.md)
- **What the tiers are**: [CRYPTO_TIERS.md](CRYPTO_TIERS.md)
- **How this board works** (DFU stages, CDC-ECM, gotchas):
  [DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md) — **read Parts A–C
  first**; this runbook does not repeat them.

This board is used because it is the **only** one where all four tiers fit:
Full PQC is physically impossible on the samr21.

> **T1–T4 need no new keys** — every signing and device key they use already
> exists in the repo (§2). **T5 (ES256) is the one exception** and has a keygen
> step; see [§2.1](#21-t5--es256-the-second-classical-baseline).

---

## 1. The three flags that make this a perf run

Every command below carries these, on **both** the flash build and the publish
build:

| Flag | Why |
|---|---|
| `SUIT_PERF=1` | compiles in the checkpoints. Without it you get a normal update and no numbers. |
| `PROGRESS_BAR=0` | the progress bar reprints on every received block over USB CDC-ACM and would dominate the transport and storage measurements. |
| the tier's crypto flags | the [matching rule](SETUP_COMMON.md#21-the-matching-rule--read-this-before-anything-fails): build flags decide what the device accepts, publish flags decide what is produced. |

**Why `SUIT_PERF=1` on the publish too:** the publish build produces the image
that lands in slot 1 and *runs after the reboot*. Omit it there and the very
next update is unmeasurable.

> **Free extra data: add `SUIT_HOST_PERF=1
> SUIT_HOST_PERF_LOG=~/suit-perf-logs/host-$TIER.csv`** to steps 3, 5 and 6 and
> the same runs also measure the **producer** side — signing, encapsulation and
> encryption on the build host — at no cost to the device measurement. Results
> and schema: [PERF_RESULTS_HOST.md](PERF_RESULTS_HOST.md),
> [PERFORMANCE.md §8](PERFORMANCE.md). For per-algorithm host comparisons use
> `dist/tools/suit/host_crypto_bench.py` instead: a single publish is one sample,
> and ML-DSA signing latency varies ~20× run to run.

---

## 2. The tiers

Row numbers refer to the
[dongle combination matrix](DEVICE_NRF52840_DONGLE.md#combination-matrix--nrf52840-dongle).

| Tier | Row | Signature | Manifest + payload enc | Key dir | `SUIT_KEY` | Device key |
|---|---|---|---|---|---|---|
| **T1 Full Classical** | 4 | Ed25519 | X25519 | `ed25519-keys` | `ed25519` | `device_x25519.pem` |
| **T2 Hybrid (PQ-enc)** | 6 | Ed25519 | **ML-KEM-768** | `ed25519-keys` | `ed25519` | `device_mlkem768.pem` |
| **T3 Hybrid (PQ-sig)** | 10 | **ML-DSA-44** | X25519 | `mldsa44-keys` | `mldsa44` | `device_x25519.pem` |
| **T4 Full PQC** | 19 | **ML-DSA-65** | **ML-KEM-768** | `mldsa-keys` | `mldsa65` | `device_mlkem768.pem` |
| **T5 Classical (ECDSA)** | — | **ES256** | X25519 | `es256-keys` | `es256` | `device_x25519.pem` |

All key directories are under `examples/advanced/suit_update/`. T5 has no
combination-matrix row because ES256 was only ever measured for ROM/RAM on the
samr21, never as a dongle combination.

> **If you only have time for three runs, do T1, T2, T4.** T3 is what lets the
> signature axis and the KEM axis be separated from each other rather than
> inferred — worth it if you can.

### 2.1 T5 — ES256, the second classical baseline

ES256 is ECDSA over NIST P-256 (COSE `-7`), the signature algorithm the wider
SUIT/COSE ecosystem treats as the default. It is **not** a security tier of its
own — it is equally broken by Shor's algorithm and belongs entirely inside the
classical tier ([CRYPTO_TIERS.md §1](CRYPTO_TIERS.md)). It earns a run for a
different reason.

**It isolates the library from the algorithm.** The measured results so far show
ML-DSA verifying 30–54× faster than Ed25519, but that comparison is confounded:
RIOT's Ed25519 runs on **c25519** (compact, byte-serial, chosen for code size
and unavoidable — its `fprime_*` symbols collide with wolfCrypt's), while ML-DSA
runs on **wolfCrypt**. ES256 is a *classical* signature that runs on
**wolfCrypt** (`libcose_crypt_wolfcrypt_ecdsa`), so it answers the question the
other four tiers cannot:

> How much of the post-quantum "speed-up" is the algorithm, and how much is
> just a faster library?

**T5 is a near-perfect control against T1.** Verified with
`make info-debug-variable-…`, these are *identical* between the two builds:

| | T1 Ed25519 | T5 ES256 |
|---|---|---|
| `SUIT_MANIFEST_BUFSIZE` | 768 | **768** |
| `SUIT_WORKER_STACKSIZE` | default 6,144 | **default 6,144** |
| wolfSSL source tree | pinned `pkg/wolfssl` | **pinned `pkg/wolfssl`** |
| KEM / device key | X25519 | **X25519** |
| Signature backend | c25519 | **wolfCrypt ECDSA** ← the only difference |

ES256 does *not* trigger the local-wolfSSL override (`suit.base.inc.mk:55` filters
only `ml-dsa-%` / `ml-kem-%`), and needs no manifest-buffer bump — its signature
is 64 B, the same size as Ed25519's. So T1 and T5 differ in the signature
backend and nothing else.

**Generate the key once** (T5 is the only tier that needs this; `es384` / `es512`
work identically if you want the curve sweep):

```bash
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/es256-keys
echo 0 | SUIT_KEY_ALGO=es256 \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/es256-keys \
  SUIT_KEY=es256 BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update suit/genkey
```

`echo 0` answers the "encryption for key file" prompt with *none*. This runs
`openssl genpkey -algorithm ec -pkeyopt ec_paramgen_curve:P-256`; no OpenSSL 3.5+
is required, unlike ML-DSA. The matching `device_x25519.pem` is generated
automatically into the same directory on the first build.

> **Already done on this machine.** `es256-keys/` now holds `es256.pem`,
> `es256.pem.pub` and `device_x25519.pem`, and the T5 configuration has been
> built once to verify it links. Re-run the command above only if you want a
> fresh key — and if you do, re-flash *before* publishing, since the verifying
> key is baked into the image.

Measured for the instrumented T5 build (`SUIT_PERF=1 PROGRESS_BAR=0`):

```
   text    data     bss     dec     hex
 112632     228   26600  139460   220c4
```

**RAM 26,828 B** (T1: 26,500 B, +328 B) and **ROM 112,632 B** (T1: 108,236 B,
+4,396 B) — wolfCrypt's ECDSA/P-256 code against c25519's. The ~4.4 KB larger
image means T5's payload differs from T1's, so as everywhere else the bulk
phases compare as **throughput**, and `sig_verify` compares directly.

---

## 3. One-time setup

Do this once, not per tier.

### 3.1 Bootloader (stage 1)

Follow [DEVICE_NRF52840_DONGLE.md Part C, Stage 1](DEVICE_NRF52840_DONGLE.md#stage-1--install-the-riotboot_dfu-bootloader-once-via-nordic-dfu)
— RESET → Nordic DFU (`1209:7d00`) → `usbipd attach` → flash `riotboot_dfu`.
**Skip if `sudo dfu-util -l` already shows `1209:7d02` with two RIOT-OS slots.**

### 3.2 Three long-running shells

```bash
cd ~/masterthesis/RIOT

# Shell A — CoAP file server
mkdir -p coaproot && aiocoap-fileserver coaproot

# Shell B — CDC-ECM network link (this is NOT a console on this board)
BOARD=nrf52840dongle make -C examples/advanced/suit_update term
```

```bash
# Shell C — host address in the delegated prefix, then keep for ad-hoc commands
ECM_IFACE=$(ls -A /sys/bus/usb/drivers/cdc_ether/*/net/); ECM_IFACE=${ECM_IFACE%/}
sudo ip address add 2001:db8::1/64 dev "$ECM_IFACE"
ping -c3 fe80::2%"$ECM_IFACE"
echo "ECM_IFACE=$ECM_IFACE"        # note this down, every notify needs it
```

### 3.3 Logging terminal

**This is the measurement instrument — do not skip the logfile.**

```bash
mkdir -p ~/suit-perf-logs
picocom -b 115200 /dev/ttyACM0 --logfile ~/suit-perf-logs/T1-full-classical.log
```

Use a **new logfile name per tier** (`T1-…` through `T5-…`). If your picocom
lacks `--logfile`:

```bash
script -f ~/suit-perf-logs/T1-full-classical.log -c "picocom -b 115200 /dev/ttyACM0"
```

Exit picocom with `Ctrl-A Ctrl-X`.

---

## 4. Per-tier procedure

Run this whole block once per tier. **Start a fresh shell for each tier** (or
run the `unset` in step 0 — stale exported flags from the previous tier are the
single most likely way to get a mislabelled measurement).

### Step 0 — Select the tier

```bash
cd ~/masterthesis/RIOT
unset SUIT_KEY_ALGO SUIT_MANIFEST_ENCRYPT_ALGO      # ALWAYS, even for T1
export SUIT_PERF=1 PROGRESS_BAR=0
export BOARD=nrf52840dongle
export ECM_IFACE=<value from §3.2>
```

Then **exactly one** of:

```bash
# ---- T1 Full Classical ----------------------------------------------------
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys
export SUIT_KEY=ed25519
export DEVKEY=$SUIT_KEY_DIR/device_x25519.pem
export ENCTOOL=examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py
export TIER=T1-full-classical

# ---- T2 Hybrid (PQ-enc) ---------------------------------------------------
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys
export SUIT_KEY=ed25519
export SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768
export DEVKEY=$SUIT_KEY_DIR/device_mlkem768.pem
export ENCTOOL=examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py
export TIER=T2-hybrid-pq-enc

# ---- T3 Hybrid (PQ-sig) ---------------------------------------------------
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa44-keys
export SUIT_KEY=mldsa44
export SUIT_KEY_ALGO=ml-dsa-44
export DEVKEY=$SUIT_KEY_DIR/device_x25519.pem
export ENCTOOL=examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py
export TIER=T3-hybrid-pq-sig

# ---- T4 Full PQC ----------------------------------------------------------
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys
export SUIT_KEY=mldsa65
export SUIT_KEY_ALGO=ml-dsa-65
export SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768
export DEVKEY=$SUIT_KEY_DIR/device_mlkem768.pem
export ENCTOOL=examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py
export TIER=T4-full-pqc

# ---- T5 Classical, ECDSA (needs the §2.1 keygen first) --------------------
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/es256-keys
export SUIT_KEY=es256
export SUIT_KEY_ALGO=es256
export DEVKEY=$SUIT_KEY_DIR/device_x25519.pem
export ENCTOOL=examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py
export TIER=T5-classical-es256
```

### Step 1 — Pre-flight (5 seconds, saves a wasted run)

```bash
make -C examples/advanced/suit_update info-modules | grep -E "^(suit_perf|malloc_monitor)$"
```

Must print **both** `malloc_monitor` and `suit_perf`. If it prints nothing,
`SUIT_PERF=1` is not in effect — stop and fix the environment.

### Step 2 — Put the dongle into DFU mode

If it is running RIOT (`1209:7d01`):

```bash
sudo dfu-util -e -d 1209:7d01
```

If that does not re-enumerate it as `1209:7d02`, press **RESET** for Nordic DFU
and redo §3.1 stage 1. Then, in **admin PowerShell**:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Confirm:

```bash
sudo dfu-util -l | grep 7d02
```

### Step 3 — Build and flash slot 0

```bash
APP_VER=$(date +%s) PROGRAMMER=dfu-util DFU="sudo dfu-util" \
  make -C examples/advanced/suit_update clean all riotboot/flash-slot0 \
  2>&1 | tee ~/suit-perf-logs/$TIER-build.log
```

**Record the `size` line** it prints (`text  data  bss …`) — that is this
tier's instrumented RAM figure. `'dfu-util' programmer is not supported by this
board` is a harmless warning.

Re-attach after it reboots (it re-enumerates as `1209:7d01`):

```powershell
usbipd attach --wsl --busid <BUSID>
```

### Step 4 — Reconnect the network and the logging terminal

```bash
# Shell B (if it exited)
BOARD=nrf52840dongle make -C examples/advanced/suit_update term

# Shell C
ECM_IFACE=$(ls -A /sys/bus/usb/drivers/cdc_ether/*/net/); ECM_IFACE=${ECM_IFACE%/}
sudo ip address add 2001:db8::1/64 dev "$ECM_IFACE"

# Logging terminal — NEW FILE FOR THIS TIER
picocom -b 115200 /dev/ttyACM0 --logfile ~/suit-perf-logs/$TIER.log
```

### Step 5 — Publish

```bash
APP_VER=$(date +%s) SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

Same flags as step 3 — they are still exported, which is the point of step 0.

### Step 6 — Encrypt the manifest

```bash
python3 $ENCTOOL --key $DEVKEY \
  -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
  coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin
```

**Record the reported container overhead** (~92 B for X25519, ~1144 B for
ML-KEM-768). `--key` must be **this tier's** `$DEVKEY` — a key from a different
directory fails much later as `manifest decryption failed: -213`.

### Step 7 — Notify, and watch the logging terminal

```bash
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  make -C examples/advanced/suit_update suit/notify
```

The update runs, the report prints, and the device reboots into slot 1. Verify
in the log:

```
suit_perf: run 1  sig=… kem=…
  phase          |  time [us] |     bytes | calls | chunks
  …
suit_worker: update successful
suit_worker: rebooting...
Running from slot 1
```

> Judge success from the **device terminal**, not the host command — `suit/notify`
> can error or hang on the host after a successful update.

### Step 8 — Recover the `PERFCFG` line

The reboot into slot 1 re-enumerates USB and **kills picocom before the new
slot's boot banner arrives** (`FATAL: read zero bytes from port`) — so the
config line is not in the log yet. Re-attach from **admin PowerShell**:

```powershell
usbipd attach --wsl --busid <BUSID>
```

then reconnect to the **same logfile** (`--logfile` appends) and type `suit_perf`
at the RIOT shell:

```bash
picocom -b 115200 /dev/ttyACM0 --logfile ~/suit-perf-logs/$TIER.log
```

```
> suit_perf
suit_perf: nrf52840dongle sig=… kem=… pubkey=… B
suit_perf: manifest_buf=… fw_hdr=… pq_scratch=… worker_stack=…
PERFCFG,nrf52840dongle,…
suit_perf: no update run recorded since boot
```

`no update run recorded since boot` is **correct** here — slot 1 has just
booted and has not run an update itself. Only the `PERFCFG` line is wanted from
this step; the measurement is already in the log from step 7.

### Step 9 — Extract the data

```bash
grep -E '^(PERFCFG|PERF|PERFMEM),' ~/suit-perf-logs/$TIER.log > ~/suit-perf-logs/$TIER.csv
cat ~/suit-perf-logs/$TIER.csv
```

Expect 1 `PERFCFG` + 14 `PERF` + 3 `PERFMEM` lines.

---

## 4a. Automated capture — steps 4–9 in one command

Everything above is the **manual** path: use it for first bring-up, for a board
you have not measured before, and whenever something is wrong and you need to
watch it. Once a tier is flashed and the link is up, the loop is automatable —
and repeats are worth having, since a single run cannot show a spread
([PERFORMANCE.md §9.3](PERFORMANCE.md)).

```bash
python3 dist/tools/suit/device_perf_bench.py --tier T4 --repeats 20
```

That replaces steps 4–9 and runs unattended: publish at a fresh sequence
number, encrypt, trigger, scrape the report, **reopen the console across the
reboot**, and recover `PERFCFG` every run. It ends with `DEVBENCH,` summary
lines (median / min / p95 / stdev per phase) and a raw per-run CSV in exactly
the form step 9 produces by hand.

**Two one-time steps first**, both needing root, neither inside the loop:

```bash
# 1. flash the tier -- runbook step 3, unchanged
# 2. keep the CDC-ECM address alive across reboots (own shell, whole session)
sudo dist/tools/suit/suit-ecm-keeper.sh
```

The re-enumeration destroys and recreates the interface, so the host address is
lost on every reboot; the keeper re-adds it so the harness never needs root.
Pre-flight checks for it and prints this command if it is missing.

**And drop the admin-PowerShell round trip.** `usbipd attach` has not required
administrator privileges since usbipd-win 4.0, so from **WSL**:

```bash
usbipd.exe attach --wsl --auto-attach --busid <BUSID>
```

`--auto-attach` re-attaches through every re-enumeration, which is what makes
steps 2, 3 and 8's "re-attach from admin PowerShell" unnecessary for a run of
repeats.

Useful flags:

| Flag | Why |
|---|---|
| `--repeats 0` | dry run: pre-flight + publish + encrypt, **no board needed** |
| `--trigger notify` | the original trigger, as a control against the default `suit fetch` |
| `--host-perf-log <path>` | also capture the producer side, giving paired host+device samples |
| `--verbose` | echo the device console, for when a run fails |

---

## 5. What to paste back

Per tier, four things. Raw text is fine — do not summarise or reformat.

1. **The CSV** — full contents of `$TIER.csv` (`PERFCFG` + 14 `PERF` + 3
   `PERFMEM` lines). Results so far:
   [PERF_RESULTS_NRF52840.md](PERF_RESULTS_NRF52840.md).
2. **The perf table** — the human-readable block, from `suit_perf: run …`
   through the `stack peak:` line.
3. **The size line** from `$TIER-build.log`, e.g.
   `text  data  bss  dec  hex  filename`.
4. **These log lines**, which cross-check the CSV against the wire format:
   ```
   suit_worker: got manifest with size N
   suit_worker: manifest decrypted (N bytes)
   suit: decrypting payload (header N bytes)
   suit: payload decrypted (N bytes)
   ```

Plus anything unexpected — a failed run is data too, and the error codes are
already tabulated in [SETUP_COMMON.md](SETUP_COMMON.md).

I will turn this into a results document alongside
[PERFORMANCE.md](PERFORMANCE.md), and fold the confirmed figures into
[FINDINGS.md](FINDINGS.md) and [CRYPTO_TIERS.md](CRYPTO_TIERS.md).

---

## 6. Reading the results — two things to know in advance

**The payload is not the same size in every tier.** ML-DSA adds ~12 KB of
`.text`, so the firmware image that gets fetched and decrypted grows with the
tier. Therefore:

- `payload_aead`, `storage_write` and `image_digest` **must be compared as
  throughput** (`bytes ÷ time_us`), never as absolute times.
- `mfst_kem`, `payload_kem` and `sig_verify` are fixed-cost per update and
  **are** directly comparable — those are the tier deltas that matter.

**Expect the invariant classes to stay flat.** `mfst_digest`, and the
throughputs of `payload_aead` / `storage_write` / `image_digest`, should be
near-identical across all tiers — they use the same ChaCha20-Poly1305 and
SHA-256 in every one. **If they drift, something about the run differed** (the
progress bar left on, a different payload, console contention) and the tier
comparison from that run should not be trusted.

Two exceptions, established by the repeat captures
([PERF_RESULTS_NRF52840.md §4](PERF_RESULTS_NRF52840.md)): the **network
residual** is host-dependent and swings ~7 % between tiers, and **the same
CPU-bound phase can differ by ~6 % between binaries** — Ed25519 verification of
identical input measures 2,142.8 ms in T1 and 2,290.0 ms in T2. That gap is
*not* a noise floor: with 50 runs per tier the phase reproduces to 0.008 %
within a build, so the difference is real and unexplained, and effects smaller
than it are still conclusions provided they are compared within one binary.

**What T5 is for.** Read `sig_verify` for T5 against T1 (both classical, both
X25519, both pinned wolfSSL — only the backend differs) and against T3/T4:

- If ES256 lands in the **tens of milliseconds**, alongside ML-DSA, then the
  headline speed-up is mostly **c25519 being slow**, not post-quantum being
  fast — and the honest thesis claim becomes "PQ signatures cost nothing in
  verification time on this platform", not "PQ is 50× faster than classical".
- If ES256 lands in the **hundreds of milliseconds or worse**, the lattice
  advantage is real and algorithmic.

Either outcome is publishable; the point is that the current data cannot
distinguish them.

---

## 7. If something goes wrong

| Symptom | Cause |
|---|---|
| `info-modules` shows no `suit_perf` | `SUIT_PERF=1` not exported in this shell |
| `Cannot open DFU device` | needs `sudo` — use `DFU="sudo dfu-util"` |
| `could not open port` after any flash | the WSL attachment dropped; re-run `usbipd attach` |
| `res=-50` (`SUIT_ERR_STORAGE`) | slot 0 was flashed monolithically — use `riotboot/flash-slot0`, not `make flash` |
| `manifest decryption failed: -213` | `--key` from the wrong key directory, or a manifest/payload encrypted for the other KEM |
| `recipient alg -25 != built-in -70768` | `SUIT_MANIFEST_ENCRYPT_ALGO` missing on the **publish** |
| `res=-5` | sequence-number replay — publish with a fresh `APP_VER` |
| no timing report at all, update fine | the running slot was built without `SUIT_PERF=1` |
| every phase reads 0 µs | `ztimer_usec` missing — should be impossible via the module |

Full list: [GOTCHAS.md](GOTCHAS.md) ·
[dongle-specific](DEVICE_NRF52840_DONGLE.md#dongle-specific-gotchas).
