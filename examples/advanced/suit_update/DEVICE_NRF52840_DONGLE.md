# nRF52840 Dongle — full post-quantum SUIT updates

Real over-the-air firmware updates on a **Nordic nRF52840 Dongle**
(`BOARD=nrf52840dongle`, Cortex-M4F, **256 KB RAM**, ~892 KB usable ROM),
driven from **WSL2**. A ~£10 USB stick — **no debug probe required**.

This is the **roomy** board, and its reason to exist in this thesis: every
crypto combination fits, including **ML-DSA-87** and the **full post-quantum
combination (ML-DSA + ML-KEM)** that the
[samr21-xpro](DEVICE_SAMR21_XPRO.md) physically cannot build. Both are
confirmed working on real hardware.

**Prerequisite:** [SETUP_COMMON.md](SETUP_COMMON.md) §1 (host tools) and §3
(a signing key). Concepts: [GUIDE.md](GUIDE.md).

---

## What is different on this board

| | |
|---|---|
| **RAM** | 256 KB — not a constraint |
| **Flashing** | **USB DFU**, two-stage (see banner) — no onboard debugger |
| **Networking** | **USB CDC-ECM** (USB Ethernet), *not* ethos |
| **Shell** | a **separate CDC-ACM** serial — `make term` does **not** open a console |
| **Device address** | `fe80::2%$ECM_IFACE` |

The dongle has **no UART-to-USB bridge**, so ethos (which rides a hardware
UART) enumerates nothing. The app `Makefile` therefore forces `USE_ETHOS=0`
for this board and brings the link up with
`dist/tools/usb-cdc-ecm/start_network.sh` (host `fe80::1`, device `fe80::2`).

> ### ❗ Do **not** use a plain `make flash` — it cannot do OTA
>
> A default `make flash` DFU-flashes the **monolithic** `suit_update.hex`: the
> board's nrfutil recipe hardcodes `--package=$(HEXFILE).zip` and ignores
> `FLASHFILE`, so the riotboot bootloader and slot headers are never written.
> riotboot is still compiled in, looks for a slot header at `SLOT0_OFFSET`,
> finds none, and every update dies at the storage layer:
> ```
> suit: validated manifest version
> suit_worker: suit_parse() failed. res=-50          # SUIT_ERR_STORAGE
> suit_worker: update failed, hdr invalid
> ```
> (Everything before that — networking, fetch, decryption, signature
> verification — works. Confirmed on hardware 2026-07-21.) Plain
> `bootloaders/riotboot` cannot rescue it either: it links to flash base 0x0,
> which the Nordic MBR owns and DFU protects.
>
> ### ✅ The working path is `riotboot_dfu` (Part C) — confirmed end-to-end 2026-07-22
>
> RIOT's own DFU bootloader builds at `ROM_OFFSET=0x1000` (above the Nordic
> MBR) and is installed *through* the existing Nordic DFU. It then exposes its
> own USB DFU (`1209:7d02`) so `dfu-util` can flash the riotboot slots — a
> valid slot exists, and OTA works. **No SWD probe needed.** (An external
> probe with `PROGRAMMER=jlink make … riotboot/flash` is an alternative, but
> unnecessary.)

---

## Part A — Connect the dongle to WSL2

The dongle presents **three different USB identities**, and each transition
drops the WSL attachment:

| State | USB ID | Used for |
|---|---|---|
| Nordic DFU bootloader | `1209:7d00` | stage-1 `nrfutil` flash |
| RIOT `riotboot_dfu` DFU mode | `1209:7d02` | stage-2 slot flash (`dfu-util`) |
| Running RIOT (composite) | `1209:7d01` | CDC-ACM shell + CDC-ECM network + DFU runtime |

1. Install `usbipd-win` on Windows and the USB/IP client in WSL (one-time).
   Confirm `lsusb` works in WSL.

2. **Enter DFU mode:** press the dongle's **RESET** button (the small side
   button). The red LED pulses slowly — that is the Nordic Open bootloader,
   `1209:7d00` in `lsusb`.

3. **Attach to WSL**, in **admin PowerShell**:
   ```powershell
   usbipd list                       # find the BUSID
   usbipd bind   --busid <BUSID>     # one-time per device
   usbipd attach --wsl --busid <BUSID>
   ```

4. Identify the node in WSL:
   ```bash
   for d in /dev/ttyACM*; do
     echo "$d:"; udevadm info -q property -n "$d" | grep -E 'ID_MODEL=|ID_MODEL_ID='
   done
   ```
   The dongle in DFU shows `ID_MODEL_ID=7d00`. This guide assumes
   `/dev/ttyACM0`; adjust if yours differs.

> **You will re-run `usbipd attach` after every flash and mode change.** The
> dongle re-enumerates each time. `bind` is one-time. This is the #1 cause of
> "could not open port" here — it is expected, not a fault.

---

## Part B — Extra prerequisites

Beyond [SETUP_COMMON.md](SETUP_COMMON.md) §1:

```bash
sudo apt-get install -y picocom dfu-util
make -C dist/tools/uhcpd          # CDC-ECM networking needs uhcpd
```

- `picocom` — the serial terminal for the RIOT shell over CDC-ACM
  (`python3 -m serial.tools.miniterm` works too).
- `dfu-util` — flashes riotboot slots through RIOT's DFU bootloader (stage 2).
- You do **not** need `dist/tools/ethos` on this board.

You also need Nordic's `nrfutil` on PATH, used **once** in stage 1:

```bash
nrfutil version    # or: pip3 install --user nrfutil
```

---

## Part C — Install riotboot_dfu, then flash slot 0

Do this **before** Part D — the firmware embeds the verifying key at flash
time, and networking only works once RIOT is running.

Pick your combination's flags from the [matrix](#combination-matrix--nrf52840-dongle)
and use them consistently. The examples below use Ed25519 defaults; substitute
your key directory.

### Stage 1 — Install the `riotboot_dfu` bootloader (once, via Nordic DFU)

With the dongle in **Nordic DFU mode** (RESET → slow red LED, `1209:7d00`) and
attached to WSL:

```bash
cd ~/masterthesis/RIOT
BOARD=nrf52840dongle PORT=/dev/ttyACM0 PREFLASHER=true \
  make -C bootloaders/riotboot_dfu clean flash
```

`PREFLASHER=true` skips the 1200-baud touch that would otherwise re-enumerate
the bootloader mid-flash and drop the usbip attachment under WSL.

This is a **one-time** step — you do not repeat it for app updates.

On reboot it finds no valid slot and enters **RIOT's DFU mode**, re-enumerating
as **`1209:7d02`**. Re-attach from Windows:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Confirm — **use `sudo`**, or libusb cannot claim the device and prints
`Cannot open DFU device 1209:7d02` (a permissions issue, not a flash failure):

```bash
sudo dfu-util -l
```

Expected — both slots as separate alt settings:

```
Found DFU: [1209:7d02] ... alt=1, name="RIOT-OS Slot 1", serial="..."
Found DFU: [1209:7d02] ... alt=0, name="RIOT-OS Slot 0", serial="..."
```

> To avoid `sudo` on every call, install a udev rule and re-attach (WSL2 does
> not always run udev, so `sudo` remains the reliable fallback):
> ```bash
> echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7d02", MODE="0666"' \
>   | sudo tee /etc/udev/rules.d/99-riotboot-dfu.rules
> sudo udevadm control --reload-rules && sudo udevadm trigger
> ```

### Stage 2 — Flash the app into slot 0 (via RIOT's DFU)

**Two things are mandatory here:** `DFU="sudo dfu-util"` (same permission
reason) and a **pinned `APP_VER`** — the slot binary is named
`slot0.$(APP_VER).bin`, and without pinning the riotboot sub-make (which
*builds* it) and the parent make (which *flashes* it) each evaluate
`date +%s` independently, so the flash looks for a file that was never built.

```bash
APP_VER=$(date +%s) \
  PROGRAMMER=dfu-util DFU="sudo dfu-util" \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys SUIT_KEY=ed25519 \
  BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update clean all riotboot/flash-slot0
```

This bakes the signing key's **public** half in and, with encryption on by
default, generates + embeds `$SUIT_KEY_DIR/device_x25519.pem` (or
`device_mlkem768.pem` for an ML-KEM build) on first build. `dfu-util` writes
alt-0; `riotboot_dfu` boots it, so RIOT comes up **with a valid slot**.

> **Harmless warning:** `'dfu-util' programmer is not supported by this board`.
> It is not in `PROGRAMMERS_SUPPORTED`, but `riotboot/flash-slot0` invokes it
> directly and the download runs. Ignore it.

> `usbus_dfu` is enabled **automatically** for this board in the app Makefile,
> so slots align with the bootloader (`SLOT0_OFFSET=0x4000`) and the running
> firmware exposes a DFU-runtime. **Never** pass it as a make argument
> (`make USEMODULE+=usbus_dfu …`) — GNU make treats that as an override and
> silently drops the app's own modules, failing with `cose/sign.h: No such file`.

### Re-attach after flashing

Booting slot 0 re-enumerates the dongle again (`1209:7d01`, the running
composite) and drops it off WSL:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Confirm both interfaces returned:

```bash
ls /dev/ttyACM*                                            # CDC-ACM shell
ls -A /sys/bus/usb/drivers/cdc_ether/*/net/ 2>/dev/null    # CDC-ECM netif
```

> If the second prints nothing, WSL's kernel did not bind `cdc_ether` — a known
> WSL2 risk for this board. The network will not come up in Part D until it does.

> **Later app updates need neither Nordic DFU nor stage 1.** A normal
> `suit/notify` (Part E) writes the other slot and reboots. To re-flash slot 0
> by wire, the running firmware's DFU-runtime lets `dfu-util` re-enter DFU —
> just re-run the stage-2 command.

---

## Part D — Network (CDC-ECM) and shell (CDC-ACM)

These are **two separate things** on this board.

### Network link

`make term` on the dongle runs the CDC-ECM network script (the board's
`TERMPROG`) — it does **not** open a console. Keep it in its own shell:

```bash
cd ~/masterthesis/RIOT
BOARD=nrf52840dongle make -C examples/advanced/suit_update term
# equivalently: sudo sh dist/tools/usb-cdc-ecm/start_network.sh 2001:db8::/64
```

This finds the `cdc_ether` interface, sets host `fe80::1`, delegates
`2001:db8::/64` to the device via `uhcpd`, and routes via the device's
`fe80::2`.

In another shell, give the host a routable address in the delegated prefix:

```bash
ECM_IFACE=$(ls -A /sys/bus/usb/drivers/cdc_ether/*/net/); ECM_IFACE=${ECM_IFACE%/}
sudo ip address add 2001:db8::1/64 dev "$ECM_IFACE"
ping -c3 fe80::2%"$ECM_IFACE"
```

So: **`SUIT_COAP_SERVER=[2001:db8::1]`** and
**`SUIT_CLIENT=[fe80::2%$ECM_IFACE]`**.

### Shell

The RIOT shell is on the CDC-ACM serial (`/dev/ttyACM*`, now showing
`ID_MODEL_ID=7d01`, *not* the DFU `7d00`). In yet another shell:

```bash
picocom -b 115200 /dev/ttyACM0
```

Use this terminal to watch update progress and to `suit fetch` directly.

### CoAP file server

Own shell, keep running:

```bash
cd ~/masterthesis/RIOT && mkdir -p coaproot && aiocoap-fileserver coaproot
```

---

## Part E — Publish and notify

Same key variables as Part C. `suit/publish` copies into
`coaproot/fw/suit_update/nrf52840dongle/`.

```bash
APP_VER=$(date +%s)
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY \
  BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

By default this publishes ChaCha20-Poly1305-encrypted `.enc` slot binaries;
append `SUIT_FIRMWARE_ENCRYPT=0` for a plaintext publish — **mandatory** if the
firmware was flashed with that flag.

> **Pin `APP_VER`** (same epoch-drift reason as stage 2), and remember it is
> also the anti-rollback sequence number — always fresh and larger.

### E.1 — Encrypt the manifest *(optional)*

```bash
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
  coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin
```

ML-KEM: `manifest-encryption-mlkem/encrypt_manifest.py --key
$SUIT_KEY_DIR/device_mlkem768.pem`. Skip entirely for plaintext manifests.

### E.2 — Notify

```bash
# plaintext manifest
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify

# encrypted manifest (after E.1)
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
```

Or from the CDC-ACM shell:

```
> suit fetch coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc
```

Watch the CDC-ACM terminal: fetch → decrypt → verify → write the inactive slot
→ reboot into it (`Running from slot 1`).

> **A SUIT update does not use DFU.** It swaps riotboot slots and boots
> directly, so unlike the initial flash it needs **no WSL re-attach** — the
> CDC-ECM link stays up across the slot switch.

---

## Combination matrix — nRF52840 Dongle

256 KB RAM removes every constraint from the samr21 matrix. **Every ❌ there
becomes ✅ here.** Apply flags to **both** the stage-2 flash and the publish.

Legend: ✅ verified on hardware · 🔄 expected (RAM math), not individually run

| # | Signature | Manifest enc | Payload enc | Flags (flash **and** publish) | Status |
|---|---|---|---|---|---|
| 1 | Ed25519 | — | — | `SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0` | 🔄 |
| 2 | Ed25519 | X25519 | — | `SUIT_FIRMWARE_ENCRYPT=0` | 🔄 |
| 3 | **Ed25519** | **—** | **X25519** | `SUIT_MANIFEST_ENCRYPT=0` | ✅ **verified 2026-07-22** — full OTA, `payload decrypted (108028 bytes)`, `Running from slot 1` |
| 4 | Ed25519 | X25519 | X25519 | *(none — the defaults)* | ✅ verified 2026-07-21 (manifest pass-through + encrypted payload) |
| 5 | Ed25519 | ML-KEM-768 | ML-KEM-768 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | 🔄 |
| 6 | Ed25519 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | 🔄 |
| 7 | ML-DSA-44 | — | — | `SUIT_KEY_ALGO=ml-dsa-44` + both `=0` | 🔄 |
| 8 | ML-DSA-44 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-44` | 🔄 |
| 9 | ML-DSA-65 | — | — | `SUIT_KEY_ALGO=ml-dsa-65` + both `=0` | 🔄 |
| 10 | ML-DSA-65 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-65` | 🔄 |
| 11 | **ML-DSA-87** | — | — | `SUIT_KEY_ALGO=ml-dsa-87` + both `=0` | 🔄 **fits here** (❌ on samr21) |
| 12 | ML-DSA-87 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-87` | 🔄 fits here |
| 13 | ML-DSA-44 | ML-KEM-768 | ML-KEM-768 | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | 🔄 (❌ on samr21) |
| 14 | **ML-DSA-65** | **ML-KEM-768** | **ML-KEM-768** | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | ✅ **FULL PQ — verified 2026-07-22** |
| 15 | ML-DSA-87 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_KEY_ALGO=ml-dsa-87 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | 🔄 maximum strength |

Rows **11** and **14** are why this board is in the thesis: the **category-5
signature** and the **full post-quantum combination** that the samr21 cannot
build both fit comfortably in 256 KB — and row 14 is hardware-confirmed, not a
RAM projection.

The app Makefile still forces the 9,216 B worker stack for any `ml-kem-*`
build (needed for wolfCrypt's ML-KEM decapsulation) — automatic, nothing to
pass.

The [matching rule](SETUP_COMMON.md#21-the-matching-rule--read-this-before-anything-fails)
applies unchanged: build flags decide what the device *can accept*, publish
flags decide what is *produced*, plaintext always passes through.

---

## Worked example — the full post-quantum combination (row 14)

Verified end-to-end on real hardware, 2026-07-22.

```bash
cd ~/masterthesis/RIOT

# 1. ML-DSA-65 key (needs OpenSSL 3.5+ — SETUP_COMMON.md §1.4)
mkdir -p examples/advanced/suit_update/mldsa-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update suit/genkey

export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys
export SUIT_KEY=mldsa65

# 2. stage-2 flash (after stage 1, dongle in 1209:7d02 DFU mode)
APP_VER=$(date +%s) PROGRAMMER=dfu-util DFU="sudo dfu-util" \
  SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY \
  SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update clean all riotboot/flash-slot0

# 3. publish — the ALGO flag is mandatory here too (it also picks the
#    payload's encryption scheme!)
APP_VER=$(date +%s)
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY \
  SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 \
  BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish

# 4. encrypt the manifest for THIS key directory's ML-KEM device key
python3 examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_mlkem768.pem \
  -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
  coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin

# 5. notify
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
```

Observed on hardware:

```
suit_worker: got manifest with size 4892
suit_worker: manifest decrypted (3748 bytes)      # 1144 B ML-KEM-768 overhead
suit: verifying manifest signature                 # ML-DSA-65
suit: validated manifest version
... vendor ID: OK ... class id: OK ... SUIT policy check OK.
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 1126 bytes)       # ML-KEM-768 payload
Fetching firmware |█████████████████████████| 100%
suit: payload decrypted (124188 bytes)
Verified installed payload
suit_worker: update successful
suit_worker: rebooting...
...
Running from slot 1
```

> **Two mistakes that cost time during this bring-up** — both now in
> [GOTCHAS.md](GOTCHAS.md#encryption--keys):
> 1. **`--key` must point at the same `SUIT_KEY_DIR` used for flashing.** A
>    different directory's `device_mlkem768.pem` is a *different keypair* and
>    gives `manifest decryption failed: -213` with no earlier error — FIPS 203
>    implicit rejection means a wrong KEM key silently produces a wrong shared
>    secret rather than a decapsulation failure.
> 2. **`SUIT_MANIFEST_ENCRYPT_ALGO` must be on the publish command too.** It
>    also governs the *payload's* auto-encryption. Omit it and the payload gets
>    encrypted for X25519 while the firmware expects ML-KEM — surfacing much
>    later as `suit: recipient alg -25 != built-in -70768`, after the manifest
>    has already decrypted fine.

---

## Dongle-specific gotchas

- **Every DFU/mode change drops the WSL attachment** — re-run
  `usbipd attach --wsl`. Expected, not a fault.
- **`PREFLASHER=true`** when already in DFU mode.
- **`dfu-util` needs root** under WSL2 usbip (`DFU="sudo dfu-util"`), or the
  udev rule.
- **`make term` is the network script, not a console.** The shell is the
  CDC-ACM serial via `picocom`. Never use ethos here.
- **Flash geometry must not depend on `PROGRAMMER`.** Already fixed in the app
  Makefile (`ROM_OFFSET=0x1000`, `ROM_LEN=0xdf000` pinned); without it a
  `dfu-util` flash and an `nrfutil` publish disagree on `SLOT1_OFFSET`
  (`0x82000` vs `0x71800`) → `res=-4`, `offset does not match`. Sanity-check:
  ```bash
  make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=dfu-util \
    info-debug-variable-SLOT1_OFFSET
  make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=nrfutil \
    info-debug-variable-SLOT1_OFFSET
  ```
  Both must print `0x71800`.
- **Re-flashing a slot needs DFU *mode*, not runtime.** Force it with
  `sudo dfu-util -e -d 1209:7d00`, re-attach, then re-run stage 2.
- **Never disable IRQs around a `printf`** — stdio is USB here, and a
  multi-line print with interrupts off deadlocks the device.
- **RESET button = Nordic DFU only** (stage 1). Slot re-flashing does not need
  it.
- **RAM is not the constraint.** If a build overflows here, suspect a
  configuration mistake, not the board.
- **The URL buffer is 128 bytes**, not 64 — SUIT apps export
  `-DCONFIG_SOCK_URLPATH_MAXLEN=128`. Short manifest names are still good
  practice, just not load-bearing.

Full list: [GOTCHAS.md](GOTCHAS.md).

---

## Bring-up history

Condensed; full detail in
[FINDINGS.md §5](FINDINGS.md#nrf52840-dongle-bring-up-2026-07-21--07-22).

| | Finding |
|---|---|
| ✅ | CDC-ECM enumerates under WSL2; manifest fetched over it |
| ✅ | Manifest pass-through + signature verification |
| ❌→✅ | `res=-50` with a monolithic `make flash` → fixed by the two-stage `riotboot_dfu` install |
| ❌→✅ | `res=-4` `offset does not match` → `PROGRAMMER`-dependent flash geometry, fixed by pinning `ROM_OFFSET`/`ROM_LEN` |
| ✅ | Full OTA with encrypted payload (2026-07-22) |
| ✅ | Full post-quantum OTA, ML-DSA-65 + ML-KEM-768 (2026-07-22) |
| 🔄 | ML-DSA-87 signing — expected to fit, not yet exercised |

---

## Next

Want to see where these combinations stop fitting? The
**[samr21-xpro](DEVICE_SAMR21_XPRO.md)** (32 KB RAM) is where the full
post-quantum combination becomes impossible.
