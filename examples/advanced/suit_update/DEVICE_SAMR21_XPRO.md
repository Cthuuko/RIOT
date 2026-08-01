# SAMR21-xpro — SUIT updates on a 32 KB constrained device

Real over-the-air firmware updates to real flash on an Atmel/Microchip
**SAMR21-xpro** (Cortex-M0+, 48 MHz, **32 KB RAM**, 256 KB flash), driven from
**WSL2** over the wired `ethos` (Ethernet-over-serial) transport.

This is the **constrained** board, and that is the point: it is where
post-quantum cryptography stops fitting. Several combinations here are
confirmed impossible — those negative results are documented as carefully as
the working ones.

**Prerequisite:** [SETUP_COMMON.md](SETUP_COMMON.md) §1 (host tools) and §3
(a signing key). Concepts: [GUIDE.md](GUIDE.md).

> This is the **tethered** guide — the board hangs off the build host and the
> update travels over the same serial cable as the shell. For the wireless
> topology (this board as an 802.15.4 node, a Raspberry Pi serving CoAP behind a
> 6LoWPAN border router), see [DEVICE_802154_PI.md](DEVICE_802154_PI.md). Get
> this one working first.

---

## What is different on this board

| | |
|---|---|
| **RAM** | 32 KB — **the binding constraint** |
| **Flashing** | onboard EDBG (CMSIS-DAP) debugger, plain `make flash` |
| **Networking** | `ethos` — Ethernet tunnelled over the same serial line as the shell |
| **Shell** | the `setup_network.sh` terminal **is** the board terminal |
| **Device address** | `fe80::2%riot0` |

Everything except the `usbipd` commands in Part A runs in the **WSL shell**.
`usbipd` runs in **Windows PowerShell as Administrator**.

---

## Part A — Connect the board to WSL2

The SAMR21-xpro's onboard **EDBG** debugger provides both the flash interface
and a USB-serial UART bridge over one micro-USB port.

1. Plug the cable into the port labelled **EDBG USB** — the one nearest the
   corner, **not** "TARGET USB". Only EDBG carries the debugger and the UART.

2. Install `usbipd-win` on Windows (one-time), in **admin PowerShell**:
   ```powershell
   winget install --exact dorssel.usbipd-win
   ```
   Close and reopen PowerShell so `usbipd` lands on PATH.

3. Find and attach the board:
   ```powershell
   usbipd list                      # look for Atmel Corp. / Microchip (EDBG)
   usbipd bind   --busid 2-4        # one-time per device, needs admin
   usbipd attach --wsl --busid 2-4  # re-run after every replug/reboot
   ```
   Keep this PowerShell open — detaching removes the device from WSL.

4. Confirm in WSL:
   ```bash
   lsusb                 # should list Atmel Corp. EDBG CMSIS-DAP
   ls /dev/ttyACM*
   udevadm info -q property -n /dev/ttyACM1 | grep ID_MODEL   # EDBG_CMSIS-DAP
   ```
   The node is usually `/dev/ttyACM0`, but **`/dev/ttyACM1` if an nRF52840
   dongle is also plugged in** (it tends to claim ACM0). This guide assumes
   `/dev/ttyACM1` — adjust to match yours.

> **If a flash later fails, check `lsusb` first.** A dropped USB/IP attachment
> looks exactly like a code or flash bug, and is not one.

---

## Part B — Start the network bridge (once per session)

`ethos` bridges the board's serial line to a host tap interface; `uhcpd`
serves the IPv6 prefix over it. Both must already be built
([SETUP_COMMON.md](SETUP_COMMON.md) §1.3), or this aborts with
`uhcpd: not found`.

In a **dedicated terminal**, keep running for the whole session:

```bash
cd ~/masterthesis/RIOT
sudo dist/tools/ethos/setup_network.sh riot0 2001:db8::/64
```

**This terminal is also the board's interactive shell** — you will watch
update progress here. Do *not* start a separate `make term`; it would fight
ethos for the serial port.

In a **second terminal**, give the host a routable address and start the CoAP
server (keep both running):

```bash
sudo ip address add 2001:db8::1/128 dev riot0
mkdir -p ~/masterthesis/RIOT/coaproot
cd ~/masterthesis/RIOT && aiocoap-fileserver coaproot
```

> Always exit the board terminal **before** stopping `setup_network.sh`, or the
> `riot0` interface leaks.

---

## Part C — Flash

> **Close the ethos/board terminal first.** Flashing while it is attached
> floods the terminal with `$` garbage (raw EDBG programming noise) and may
> require restarting the session. Reopen it after flashing.

Pick your combination's flags from the [matrix](#combination-matrix--samr21-xpro)
below and append them to this command. The default (Ed25519 + X25519 manifest
and payload encryption) needs none:

```bash
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/ed25519-keys \
  SUIT_KEY=ed25519 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

This bakes the signing key's **public** half into the firmware and, with
encryption on, generates and embeds `$SUIT_KEY_DIR/device_x25519.pem` on the
first build.

A successful flash ends with `Done flashing` and detects
`ATMEL EDBG CMSIS-DAP … Target: SAM R21G18`.

> A single `verification failed … at address 0x1004` line **before**
> `Programming` is normal — that is `edbg` comparing the old contents. The
> real check is `Verification.... done.` afterwards.

> **First flash may abort** with `make -C .../edbg/bin: No such file or
> directory` while fetching the `edbg` flasher. Re-run the same command.

Now reopen the ethos terminal (Part B) and confirm reachability from a third
terminal:

```bash
ping -c3 fe80::2%riot0
```

---

## Part D — Publish and notify

Same key variables as the flash. **Pin `APP_VER` once** — it is both the
artifact timestamp and the anti-rollback sequence number.

```bash
APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/ed25519-keys \
  SUIT_KEY=ed25519 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

Artifacts land in `coaproot/fw/suit_update/samr21-xpro/` (look for the
`published … as coap://…` lines) — **not** at the `coaproot/` root.

**With `SUIT_FIRMWARE_ENCRYPT=1` (the default) the slot binaries are published
encrypted** and the manifest URIs point at the `.enc` files. Append
`SUIT_FIRMWARE_ENCRYPT=0` for the classic plaintext publish — and you **must**
do so if the firmware was flashed with that flag.

### D.1 — Encrypt the manifest *(only for manifest-encryption recipes)*

Not automated. Encrypt in place, for the device key of the **same
`$SUIT_KEY_DIR` you flashed with**:

```bash
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/samr21-xpro/riot.suit.enc \
  coaproot/fw/suit_update/samr21-xpro/riot.suit.latest.bin
```

Keep the name exactly `riot.suit.enc`. ML-KEM recipes use
`manifest-encryption-mlkem/encrypt_manifest.py --key
$SUIT_KEY_DIR/device_mlkem768.pem` instead.

### D.2 — Notify

```bash
# plaintext manifest
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify

# encrypted manifest (after D.1)
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Or from the board shell:

```
> suit fetch coap://[2001:db8::1]/fw/suit_update/samr21-xpro/riot.suit.enc
```

Watch the **board terminal**, not the host command. A real successful run
(2026-07-19, Ed25519 + encrypted manifest):

```
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/samr21-xpro/riot.suit.enc"
suit_worker: got manifest with size 577
suit_worker: manifest decrypted (485 bytes)
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: 1784474810, highest available: 1784474752
...vendor ID: OK ... class id: OK ... SUIT policy check OK.
riotboot_flashwrite: initializing update to target slot 1
Fetching firmware |█████████████████████████| 100%
Verified installed payload
suit_worker: update successful
suit_worker: rebooting...
...
Running from slot 1
```

`Running from slot 1` is the proof. To update again, repeat Part D with a
fresh `APP_VER`.

> ML-DSA builds pause visibly at `suit: verifying manifest signature` — that
> is lattice math on a 48 MHz M0+, not a hang.

---

## Combination matrix — samr21-xpro

**This is the board where combinations fail.** All RAM figures are link-level,
out of 32,768 B, **corrected 2026-07-20** — see
[FINDINGS.md §4](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery) for why the
earlier numbers were wrong.

Apply the flags to **both** the flash (Part C) and publish (Part D) commands,
and use the matching key directory.

Legend: ✅ verified on hardware · ⚠️ links but runtime unproven ·
❌ does not link

**Every row was re-built from a clean `BINDIR` on 2026-07-23** (`ld` link
status + `size` on `slot0.elf`); the RAM/overflow figures below are that
sweep's output and reproduced the earlier numbers byte-for-byte.

**These are ethos-mode figures.** In 802.15.4 radio mode (`USE_ETHOS=0`) the
board uses ~1.6 KB less RAM but ~12 KB more ROM, and the 128,768 B riotboot
slot becomes the binding limit — rows 8a, 9 and 11 stop fitting there. Separate
matrix: [DEVICE_802154_PI.md](DEVICE_802154_PI.md#combination-matrix--radio-mode).

| # | Signature | Manifest enc | Payload enc | Flags (flash **and** publish) | RAM | Verdict | Steps |
|---|---|---|---|---|---|---|---|
| 1 | Ed25519 | — | — | `SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0` | 20,992 B (11.5 KB spare) | ✅ **verified** | A–D, skip D.1 |
| 2 | Ed25519 | X25519 | — | `SUIT_FIRMWARE_ENCRYPT=0` | 21,160 B (11.6 KB spare) | ✅ **verified 2026-07-19** | A–D incl. D.1 |
| 3 | Ed25519 | — | X25519 | `SUIT_MANIFEST_ENCRYPT=0` | 21,424 B (11.1 KB spare) | ⚠️ links | A–D, skip D.1 |
| 4 | Ed25519 | X25519 | X25519 | *(none — the defaults)* | 21,552 B (11.2 KB spare) | ⚠️ links | A–D incl. D.1 |
| 5 | Ed25519 | ML-KEM-768 | — | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 SUIT_FIRMWARE_ENCRYPT=0` | 29,328 B (3,440 B spare) | ⚠️ links | A–D incl. D.1† |
| 6 | **Ed25519** | **ML-KEM-768** | **ML-KEM-768** | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | 30,736 B (**2,032 B spare**) | ⚠️ links — **recommended PQ-encryption demo** | A–D incl. D.1† |
| 7 | Ed25519 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | 32,720 B (**48 B spare**) | ⚠️ zero margin — one-off only | A–D incl. D.1† |
| 8 | ML-DSA-44 | — | — | `SUIT_KEY_ALGO=ml-dsa-44` + both `=0` | 30,760 B (2,008 B spare) | ✅ **verified** | A–D, skip D.1 |
| 8a | ML-DSA-44 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_FIRMWARE_ENCRYPT=0` | 30,888 B (1,880 B spare) | ⚠️ links | A–D incl. D.1 |
| 9 | ML-DSA-44 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-44` | 31,280 B (1,488 B spare) | ⚠️ links | A–D incl. D.1 |
| 10 | ML-DSA-65 | — | — | `SUIT_KEY_ALGO=ml-dsa-65` + both `=0` | 32,552 B (~216 B spare) | ✅ **verified 2026-07-18** | A–D, skip D.1 |
| 11 | ML-DSA-65 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_FIRMWARE_ENCRYPT=0` | 32,680 B (**88 B spare**) | ⚠️ links, zero tolerance | A–D incl. D.1 |
| 12 | ML-DSA-65 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-65` | **overflow 308 B** | ❌ — use #11 | — |
| 13 | ML-DSA-44 | ML-KEM-768 | — | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | **overflow 3,364 B** | ❌ **infeasible** | — |
| 14 | **ML-DSA-44** | **ML-KEM-768** | **ML-KEM-768** | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | **overflow 3,556 B** | ❌ **full PQ: infeasible** | — |
| 15 | ML-DSA-65 | ML-KEM-768 | ML-KEM-768 | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | **overflow 6,324 B** | ❌ infeasible | — |
| 16 | ML-DSA-87 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_KEY_ALGO=ml-dsa-87 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | **overflow 10,644 B** | ❌ infeasible | — |
| 17 | ML-DSA-87 | — | — | `SUIT_KEY_ALGO=ml-dsa-87` + both `=0` | **overflow 3,628 B** | ❌ never links | — |
| 18 | ML-DSA-87 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-87 SUIT_FIRMWARE_ENCRYPT=0` | **overflow 3,756 B** | ❌ never links | — |
| 19 | ML-DSA-87 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-87` | **overflow 4,148 B** | ❌ never links | — |

† ML-KEM recipes: encrypt with `manifest-encryption-mlkem/encrypt_manifest.py
--key $SUIT_KEY_DIR/device_mlkem768.pem` (or `…1024`). The app Makefile forces
a **9,216 B worker stack** for any `ml-kem-%` build — mandatory, automatic,
nothing to pass.

### Reading this matrix

- **You can have a post-quantum signature, or post-quantum encryption — not
  both.** Rows 13–16 are the thesis's central negative result: ML-DSA + ML-KEM
  exceeds 32 KB by ~3.5 KB *with every available wolfCrypt memory-reduction
  flag enabled*. This is not a tuning problem and will not be fixed by
  configuration. Row 14 **is** verified working on the
  [nRF52840 Dongle](DEVICE_NRF52840_DONGLE.md).
- **Row 6 is the samr21 PQ-encryption demo**; **rows 8/10 are the PQ-signature
  demos.**
- **ML-DSA-87 never fits at all** (rows 17–19), at any encryption setting.
  Key generation works; the link step is what fails, before anything reaches
  the board:
  ```
  ld: .../slot0.elf section `.bss' will not fit in region `ram'
  ld: region `ram' overflowed by 3628 bytes
  ```
  That error is the expected, correct outcome — not a broken build.
- **Rows 7 and 11 have essentially no margin** (48 B and 88 B). Anything that
  grows `.bss` will break them.

Full measurements and methodology: [FINDINGS.md §3.1](FINDINGS.md#31-samr21-xpro--32-kb-ram-the-constrained-case).

---

## Worked example — ML-DSA-65 signing (row 10)

The differences from the default flow are the key directory and
`SUIT_KEY_ALGO` on **every** command.

```bash
cd ~/masterthesis/RIOT

# 1. key (needs OpenSSL 3.5+ — see SETUP_COMMON.md §1.4)
mkdir -p examples/advanced/suit_update/mldsa-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update suit/genkey

# 2. flash (close the ethos terminal first)
SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4

# 3. publish (identical flags!)
APP_VER=$(date +%s)
SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish

# 4. notify
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

The manifest is now ~3.7 KB (a 3309-byte signature vs Ed25519's 64), and the
verification step pauses for a few seconds. The RAM tuning that makes this fit
(static `MlDsaKey`, `WOLFSSL_MLDSA_ASSIGN_KEY`, sized buffers) is already in
the app Makefile and applies automatically — the story behind it is in
[FINDINGS.md §5](FINDINGS.md#ml-dsa-on-samr21-three-fixes-2026-07-18).

Swap `65` → `44` throughout for row 8 (smaller signature, more headroom).

---

## samr21-specific gotchas

- **Close the terminal before flashing** — otherwise `$` garbage floods the
  session.
- **Never run `make term` under ethos** — the `setup_network.sh` shell *is*
  the board terminal.
- **Exit the board terminal before killing `setup_network.sh`**, or `riot0`
  leaks.
- **Use the EDBG port**, not TARGET USB.
- **Re-run `usbipd attach`** after every replug or reboot.
- **RAM headroom is the constraint.** Rows 7 and 11 have <100 B spare;
  anything that grows `.bss` (bigger network buffers, extra shell commands
  with static state) breaks them. Do not add unguarded debug shell commands —
  one has overflowed this board before.
- **Don't run two examples' terminals at once** — they all want the same
  `/dev/ttyACM*`.
- **Wireless (802.15.4/BLE) instead of ethos** is possible but needs a border
  router; see [README.hardware.md](README.hardware.md).

Full list: [GOTCHAS.md](GOTCHAS.md).

---

## Next

Want the combinations this board cannot run — ML-DSA-87, or full post-quantum?
Those are verified on the **[nRF52840 Dongle](DEVICE_NRF52840_DONGLE.md)**
(256 KB RAM, ~£10, no debug probe needed).
