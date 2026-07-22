# SUIT Update — nRF52840 Dongle over WSL2

Recipe for running the SUIT firmware update flow on a **Nordic nRF52840 Dongle**
(`BOARD=nrf52840dongle`), driven from **WSL2** (Ubuntu).

This is the companion of [SAMR21_EXAMPLES.md](SAMR21_EXAMPLES.md) (real
samr21-xpro over ethos) and [NATIVE_SETUP.md](NATIVE_SETUP.md) (no hardware).
It shares their signing-algorithm and encryption options — read those for the
deep detail; this file only documents what is **different on the dongle**:

1. **Flashing goes through the USB DFU bootloader** with `nrfutil`, not an
   onboard debugger. Every mode change re-enumerates USB and drops the WSL
   `usbip` attachment, so you re-attach from Windows several times. That is
   expected, not a fault.
2. **Networking is USB CDC-ECM, not ethos.** The dongle has **no UART-to-USB
   bridge**, so ethos (which rides a hardware UART) enumerates nothing on its
   USB. The app's `Makefile` therefore forces `USE_ETHOS=0` for this board and
   brings the link up over **USB CDC-ECM** (USB Ethernet) with the shell on a
   separate **CDC-ACM** serial — driven by `dist/tools/usb-cdc-ecm/start_network.sh`
   (host `fe80::1`, device `fe80::2`), not `dist/tools/ethos/setup_network.sh`.
3. **RAM and flash are roomy.** The nRF52840 has **256 KB RAM** and ~892 KB
   usable ROM (0xdf000, the rest reserved for the Nordic bootloader), vs the
   samr21-xpro's 32 KB RAM. So the RAM-fit caveats that dominate
   SAMR21_EXAMPLES.md **do not apply here**: every signing/encryption
   combination — including the **full-PQ combo (ML-DSA + ML-KEM) that does not
   fit the samr21 at all** — is expected to build and flash on the dongle. This
   is the board to use for a full post-quantum demonstration.

> **❗ DO NOT use a plain `make flash` for SUIT on this board — it cannot do OTA.**
> A default `make flash` DFU-flashes the **monolithic** `suit_update.hex`: the
> board's nrfutil recipe hardcodes `--package=$(HEXFILE).zip` and ignores
> `FLASHFILE`, so the riotboot bootloader + slot headers are never written.
> `riotboot` is still compiled in and looks for a slot header at
> `SLOT0_OFFSET=0x2000`, finds none, and every update dies at the storage layer:
> ```
> suit: validated manifest version
> suit_worker: suit_parse() failed. res=-50          # SUIT_ERR_STORAGE
> suit_worker: update failed, hdr invalid
> ```
> (`suit_storage_get_highest_seq_no()` finds no valid slot. Everything up to
> here — CDC-ECM networking, manifest fetch, decryption pass-through, signature
> verification — works; confirmed on hardware 2026-07-21.) The plain
> `bootloaders/riotboot` can't rescue this either: it links to flash base 0x0,
> which the Nordic MBR owns and DFU protects.
>
> **✅ The working path is `riotboot_dfu` — RIOT's own DFU bootloader (Part C
> below), CONFIRMED END-TO-END ON REAL HARDWARE (2026-07-22).** Unlike plain
> riotboot, it builds at `ROM_OFFSET=0x1000` (above the Nordic MBR) and is
> installed *through the existing Nordic DFU*. It then exposes its own USB DFU
> (VID/PID `1209:7d02`) to flash the riotboot slots with `dfu-util`, so a valid
> slot exists and SUIT OTA works — no SWD probe needed. A full cycle — stage 1
> bootloader install, stage 2 slot0 flash, CoAP fetch over CDC-ECM, signature
> verification, encrypted-payload decrypt, flash to slot 1, and reboot into it
> (`Running from slot 1`) — has been run on a real dongle; see Part G for the
> log. (An external SWD probe with `PROGRAMMER=jlink make ... riotboot/flash`
> is an alternative but is not needed.)

**Encryption is on by default** (`SUIT_MANIFEST_ENCRYPT=1`,
`SUIT_FIRMWARE_ENCRYPT=1`), exactly as in the sibling docs: a default build
embeds an auto-generated device key and `suit/publish` encrypts the firmware
payload. The manifest is *not* auto-encrypted (that is the manual Part F.2
step); an encryption-capable firmware also accepts plaintext manifests and
payloads (pass-through). Opt out with `SUIT_MANIFEST_ENCRYPT=0` /
`SUIT_FIRMWARE_ENCRYPT=0` — see [SAMR21_EXAMPLES.md](SAMR21_EXAMPLES.md)'s
Variant cookbook and matching rule, which apply unchanged.

---

## Part A — Connect the dongle to WSL2

The dongle enumerates two different ways:

| State | USB ID | Linux node | Used for |
|---|---|---|---|
| DFU bootloader | `1209:7d00` | `/dev/ttyACM*` | flashing with `nrfutil` |
| Running RIOT | `1209:7d01`* | `/dev/ttyACM*` **+ a `cdc_ether` netif** | shell (CDC-ACM) + network (CDC-ECM) |

\*RIOT's composite CDC-ACM+CDC-ECM PID; the exact value may differ, but it is a
*different* device from the bootloader, so WSL treats it as a new attachment
each time.

1. Install `usbipd-win` on Windows and the USB/IP client in WSL (one-time).
   Confirm WSL sees USB devices with `lsusb`.

2. **Enter DFU mode:** press the dongle's **RESET** button (the small side
   button). The red LED pulses slowly — that's the Nordic Open bootloader. In
   `lsusb` it appears as `1209:7d00 nRF52840 Dongle`.

3. **Attach it to WSL.** In an **admin PowerShell**:
   ```powershell
   usbipd list                       # find the BUSID of the nRF52840 Dongle
   usbipd bind   --busid <BUSID>     # one-time per device
   usbipd attach --wsl --busid <BUSID>
   ```

4. Confirm in WSL and note which node it is (it may not be `ttyACM0` if other
   serial devices are attached):
   ```bash
   for d in /dev/ttyACM*; do
     echo "$d:"; udevadm info -q property -n "$d" | grep -E 'ID_MODEL=|ID_MODEL_ID='
   done
   ```
   The dongle in DFU shows `ID_MODEL_ID=7d00`. Use that node as `PORT` below
   (this guide assumes `/dev/ttyACM0`; adjust if yours differs).

> **You will repeat step 3 (`usbipd attach`) after every DFU flash**, because
> the dongle re-enumerates when it switches between the bootloader and RIOT.
> `bind` only needs doing once.

---

## Part B — Prerequisites (one-time, in WSL)

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
sudo apt-get install -y gcc-arm-none-eabi picocom dfu-util
```

`picocom` is the serial terminal used for the RIOT shell over CDC-ACM (Part D);
`python3 -m serial.tools.miniterm` works too if you prefer not to install it.
`dfu-util` flashes the riotboot slots through RIOT's own DFU bootloader
(Part C stage 2).

You also need Nordic's `nrfutil` on PATH (used **once**, to install the
`riotboot_dfu` bootloader in Part C stage 1). Verify:

```bash
nrfutil version    # or: which nrfutil
```

If missing, install the classic Python `nrfutil` (provides the `dfu` command
RIOT uses), e.g. `pip3 install --user nrfutil`.

For the **post-quantum** variants (ML-DSA signing, ML-KEM encryption) you also
need **OpenSSL 3.5+** and a `cryptography` build with ML-DSA support, plus the
local wolfssl checkout — all auto-selected by the build. See
[SAMR21_EXAMPLES.md](SAMR21_EXAMPLES.md) Examples B–D for the details; nothing
board-specific here.

Build the host networking tools once:

```bash
cd RIOTBASE
make -C dist/tools/uhcpd
```

(Unlike the samr21 flow, you do **not** need `dist/tools/ethos` on the dongle —
CDC-ECM uses `dist/tools/usb-cdc-ecm/start_network.sh`, which pulls in `uhcpd`.)

---

## Part C — Signing key, then install riotboot_dfu + flash slot0

This board needs a **two-stage** install (see the banner): first put RIOT's
`riotboot_dfu` bootloader on the dongle via the Nordic DFU (stage 1), then flash
the app into slot 0 via *RIOT's* DFU (stage 2). A plain `make flash` produces a
monolithic image with no working slots and OTA fails with `res=-50`.

Do this before Part D so the firmware embeds the right verifying key. Networking
(Part D) only works once RIOT is running.

### Signing key (ed25519)

The verifying public key is baked into the firmware at flash time and must match
the key you later sign updates with. Use a dedicated ed25519 key (don't
overwrite `default.pem`, which may be an ML-DSA key from other experiments):

```bash
cd RIOTBASE
mkdir -p examples/advanced/suit_update/ed25519-keys
dist/tools/suit/gen_key.py examples/advanced/suit_update/ed25519-keys/ed25519.pem
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys
export SUIT_KEY=ed25519
```

> For ML-DSA signing use `SUIT_KEY_ALGO=ml-dsa-44|-65|-87` and a matching key
> dir/name, exactly as in SAMR21_EXAMPLES.md Examples B/C/D. **All three fit the
> dongle's 256 KB RAM** (ML-DSA-87 overflows the samr21 but not this board).

### Stage 1 — Install the `riotboot_dfu` bootloader (via Nordic DFU, once)

With the dongle in **Nordic DFU mode** (RESET → slow red LED, `1209:7d00`) and
attached to WSL (Part A), flash RIOT's DFU bootloader. `PREFLASHER=true` is the
same WSL trick as before — it skips the 1200-baud touch that would otherwise
re-enumerate the bootloader mid-flash and drop the `usbip` attachment:

```bash
BOARD=nrf52840dongle PORT=/dev/ttyACM0 PREFLASHER=true \
  make -C bootloaders/riotboot_dfu clean flash
```

`riotboot_dfu` links at `ROM_OFFSET=0x1000` (above the Nordic MBR), so nrfutil
installs it as the "application"; the Nordic MBR keeps working and now chains to
riotboot_dfu. This is a **one-time** step — you don't repeat it for app updates.

On reboot it finds no valid slot and **enters RIOT's DFU mode**: the device
re-enumerates as **`1209:7d02`** (`lsusb` shows `Generic USB device`). It
**drops off WSL** on every re-enumeration, so re-attach from Windows:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Confirm dfu-util sees it — **use `sudo`**, or libusb can't claim the device and
prints `Cannot open DFU device 1209:7d02` (a permissions issue, not a flashing
failure):

```bash
sudo dfu-util -l
```

Expected (confirmed on hardware 2026-07-21) — both slots as separate alt
settings:

```
Found DFU: [1209:7d02] ... alt=1, name="RIOT-OS Slot 1", serial="..."
Found DFU: [1209:7d02] ... alt=0, name="RIOT-OS Slot 0", serial="..."
```

> To avoid `sudo` on every dfu-util call, install a udev rule and re-attach the
> dongle (note: WSL2 doesn't always run udev — `sudo` is the reliable fallback):
> ```bash
> echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7d02", MODE="0666"' \
>   | sudo tee /etc/udev/rules.d/99-riotboot-dfu.rules
> sudo udevadm control --reload-rules && sudo udevadm trigger
> ```

### Stage 2 — Flash the app into slot 0 (via RIOT DFU, `dfu-util`)

Flash slot 0 with `PROGRAMMER=dfu-util`. **Two things are mandatory here:**
`DFU="sudo dfu-util"` (same permission reason as above) and a **pinned
`APP_VER`** — the slot binary is named `slot0.$(APP_VER).bin`, and without
pinning the riotboot sub-make (which *builds* the slot) and the parent make
(which *flashes* it) each evaluate `date +%s` independently, so the flash looks
for a filename that was never built (`dfu-util: Could not open file
... slot0.<epoch>.bin`):

```bash
APP_VER=$(date +%s) \
  PROGRAMMER=dfu-util DFU="sudo dfu-util" \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys SUIT_KEY=ed25519 \
  BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update clean all riotboot/flash-slot0
```

This bakes the key's **public** half into the firmware and (encryption being
on by default) generates + embeds the device key
`$SUIT_KEY_DIR/device_x25519.pem` (or `device_mlkem768.pem` etc. for a
`SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-*` build) on first build. `dfu-util` writes
to alt-0 (slot 0); `riotboot_dfu` then boots it, so RIOT comes up **with a valid
riotboot slot** and OTA will work.

> **Harmless warning:** the build prints `'dfu-util' programmer is not supported
> by this board. Supported programmers: 'nrfutil openocd bmp jlink'`. `dfu-util`
> isn't in the board's `PROGRAMMERS_SUPPORTED` list, but `riotboot/flash-slot0`
> invokes it directly regardless, so the download still runs. Ignore it.

> The app's `Makefile` enables `usbus_dfu` automatically for `nrf52840dongle`, so
> the slots always line up with the riotboot_dfu bootloader
> (`SLOT0_OFFSET=0x4000`) and the running firmware exposes a DFU-runtime for
> re-flashing — you don't pass any module flag by hand. (This is why the USB
> device is a three-function composite: CDC-ACM shell + CDC-ECM network + DFU.)

### Re-attach after flashing

Booting slot 0 re-enumerates the dongle again (now the running RIOT composite:
CDC-ACM shell + CDC-ECM network + DFU runtime) and **drops off WSL**. Re-attach:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Then confirm both interfaces are back in WSL:

```bash
ls /dev/ttyACM*                                   # CDC-ACM shell serial is back
ls -A /sys/bus/usb/drivers/cdc_ether/*/net/ 2>/dev/null   # CDC-ECM netif name
```

If the second command prints nothing, WSL's kernel isn't binding `cdc_ether` to
the passed-through interface — a known risk for this board under WSL. The
network won't come up in Part D until this shows an interface.

> **Later app updates** don't need Nordic DFU or stage 1 again. A normal SUIT
> `suit/notify` (Part F) writes the other slot and reboots. To re-flash slot 0
> by wire instead, the running firmware's DFU-runtime lets `dfu-util` re-enter
> DFU automatically — just re-run the stage-2 command.

---

## Part D — Bring up the network over USB CDC-ECM

Unlike ethos, `make term` on the dongle does **not** open a serial console — it
runs the CDC-ECM network script (that's the board's `TERMPROG`). So networking
and the shell are two separate things here.

### Network link

Start the CDC-ECM network (needs sudo; keep it running in its own shell):

```bash
cd RIOTBASE
BOARD=nrf52840dongle make -C examples/advanced/suit_update term
# equivalently: sudo sh dist/tools/usb-cdc-ecm/start_network.sh 2001:db8::/64
```

This finds the `cdc_ether` interface, sets host `fe80::1`, delegates the
`2001:db8::/64` prefix to the device via `uhcpd`, and routes it via the device's
`fe80::2`. So **the device is `fe80::2`** on the CDC-ECM link.

Give the host a routable address in the delegated prefix so the device can reach
the CoAP server (parallels the samr21 `ip address add` step). In another shell:

```bash
ECM_IFACE=$(ls -A /sys/bus/usb/drivers/cdc_ether/*/net/); ECM_IFACE=${ECM_IFACE%/}
sudo ip address add 2001:db8::1/64 dev "$ECM_IFACE"     # ⚠️ draft: confirm reachability
ping -c3 fe80::2%"$ECM_IFACE"
```

So on the dongle: **`SUIT_CLIENT=[fe80::2%$ECM_IFACE]`** and
**`SUIT_COAP_SERVER=[2001:db8::1]`**. (If `2001:db8::1` proves unroutable from
the device, fall back to the host link-local `fe80::1%$ECM_IFACE` as the server
and record what worked — this addressing is the main thing the bring-up
checklist must nail down.)

### Shell (separate CDC-ACM serial)

The RIOT shell is on the dongle's CDC-ACM serial (a `/dev/ttyACM*` node, now
showing `ID_MODEL_ID=7d01`, *not* the DFU `7d00`). Open it in yet another shell:

```bash
picocom -b 115200 /dev/ttyACM0      # or: python3 -m serial.tools.miniterm /dev/ttyACM0 115200
```

Use this terminal to watch update progress and to `suit fetch ...` directly.

---

## Part E — Start the CoAP file server

Keep running in its own shell:

```bash
cd RIOTBASE
mkdir -p coaproot
aiocoap-fileserver coaproot
```

---

## Part F — Publish and notify an update

Keep the `SUIT_KEY_DIR` / `SUIT_KEY` exports from Part C set (same key as the
flash). `suit/publish` copies artifacts into
`coaproot/fw/suit_update/nrf52840dongle/`, and the notify URL is built from
`SUIT_COAP_SERVER` + that path.

> **Pin `APP_VER`.** The manifest sequence number defaults to `$(date +%s)`,
> evaluated independently by the parent and riotboot sub-make; a few seconds'
> drift makes the generator look for a slot binary that doesn't exist
> (`FileNotFoundError: ... slot0.<epoch>.bin`). Pin it once per publish.

> **Watch the 64-char URL budget.** The worker's URL buffer is 64 chars and
> truncates silently. `nrf52840dongle` (14 chars) is longer than `samr21-xpro`,
> so paths are tighter here — keep the encrypted-manifest name short
> (`riot.suit.enc`).

### F.1 — Publish (payload encrypted by default)

`suit/publish` rebuilds the slot binaries, but the app Makefile's automatic
`usbus_dfu` for this board keeps them slot-aligned with the bootloader — no
extra flag needed:

```bash
APP_VER=$(date +%s)
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY \
  BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

By default this publishes the slot binaries as ChaCha20-Poly1305-encrypted
`.enc` files (the flashed default firmware decrypts them transparently). For the
classic **plaintext** payload publish, append `SUIT_FIRMWARE_ENCRYPT=0` — and
you **must** do so if the firmware was flashed with `SUIT_FIRMWARE_ENCRYPT=0`
(it can't decrypt payloads; the fetch aborts with `Image beyond size`).

### F.2 — (optional) Encrypt the manifest

Manifest encryption is *not* automated by `suit/publish`. To exercise it,
encrypt the published manifest **for this board's device key**, in place:

```bash
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/nrf52840dongle/riot.suit.enc \
  coaproot/fw/suit_update/nrf52840dongle/riot.suit.latest.bin
```

Keep the output name exactly `riot.suit.enc` (URL-length budget). For ML-KEM use
`manifest-encryption-mlkem/encrypt_manifest.py --key
$SUIT_KEY_DIR/device_mlkem768.pem`. Skip this step entirely for a plaintext
manifest (an encryption-capable firmware passes it through).

### F.3 — Notify the device

Plaintext manifest (default publish, no F.2):

```bash
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
```

Encrypted manifest (after F.2) — point notify at the encrypted name:

```bash
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%$ECM_IFACE] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
```

Or trigger straight from the board shell (Part D):

```
> suit fetch coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.enc
```

Watch the CDC-ACM shell: the dongle fetches, decrypts (if encrypted), verifies
the signature, writes the new image to the inactive riotboot slot, and reboots
into it (`Running from slot 1`). Expected extra log lines with encryption on:

```
suit_worker: manifest decrypted (N bytes)     # or: manifest not encrypted, passing through
suit: decrypting payload (header 74 bytes)     # payload encryption
suit: payload decrypted (N bytes)
suit_worker: update successful
```

> **A SUIT update does not use DFU.** It writes to the *other riotboot slot* and
> boots it directly — so unlike the initial `nrfutil` flash, `suit/notify` needs
> **no WSL re-attach**; the CDC-ECM link stays up across the slot switch.

To update again, re-run Part F with a fresh `APP_VER=$(date +%s)`. Re-sending an
already-installed sequence number is correctly rejected:

```
Manifest seq_no: <n>, highest available: <n>
seq_nr <= running image
suit_worker: suit_parse() failed. res=-5
```

That `res=-5` is the anti-rollback check working, not a bug.

---

## Signing & encryption variants (all fit this board)

The dongle's 256 KB RAM removes every RAM-fit constraint from
SAMR21_EXAMPLES.md. Select variants exactly as there — the flags are
board-independent; only the flash step differs (DFU + `PREFLASHER=true`) and the
networking is CDC-ECM. Highlights:

| Variant | Flags (on flash **and** publish) | samr21 | nrf52840dongle |
|---|---|---|---|
| Ed25519, no encryption | `SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0` | ✅ | ✅ (expected) |
| Ed25519 + X25519 (default) | *(none)* | ✅ | **✅ verified 2026-07-21** (manifest pass-through + encrypted payload) |
| ML-DSA-44/-65 signing | `SUIT_KEY_ALGO=ml-dsa-44` / `-65` | ✅ | ✅ (expected; -65 built as part of the full-PQ row below) |
| **ML-DSA-87 signing** | `SUIT_KEY_ALGO=ml-dsa-87` | ❌ overflow | **✅ fits** (expected) |
| Ed25519 + ML-KEM-768/-1024 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` / `-1024` | ✅ tight | ✅ (expected) |
| **Full PQ: ML-DSA-65 + ML-KEM-768** | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | ❌ ~3.5 KB short (ML-DSA-44 combo) | **✅ VERIFIED 2026-07-22** — manifest + payload both ML-KEM-768-encrypted, full OTA cycle, `Running from slot 1` |

The two bold rows are the dongle's reason to exist for this thesis: the
**category-5 signature** and the **full post-quantum combo** that the samr21
physically cannot build both fit comfortably in 256 KB — and the full-PQ combo
is now hardware-confirmed, not just a RAM-math projection (see Part G item 9).
The app Makefile still forces the 9,216 B worker stack for any `ml-kem-*` build
(needed for wolfCrypt's ML-KEM decapsulation — see the ML-KEM gotcha in
[SAMR21_EXAMPLES.md](SAMR21_EXAMPLES.md)); nothing extra to do here.

For the full flag matrix, the matching rule (build flags decide what the device
*can* accept; publish flags decide what's *produced*; plaintext always passes
through), and per-combo notes, follow SAMR21_EXAMPLES.md's **Variant cookbook**
verbatim — every RAM ❌ there becomes ✅ on the dongle.

---

## Part G — Bring-up status

Observed on real hardware (2026-07-21, DFU flash + CDC-ECM):

1. ✅ **CDC-ECM enumerates under WSL2** and the manifest is fetched over it —
   the log reaches `suit_worker: got manifest with size 499`, so `cdc_ether`
   binding, addressing, and CoAP transport all work.
2. ✅ **Host ↔ device reachability + CoAP server at `[2001:db8::1]`** — the
   device downloaded `coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/riot.suit.latest.bin`.
3. ✅ **Manifest pass-through + signature verification** — `manifest not
   encrypted, passing through`, `verifying manifest signature`, `validated
   manifest version` all succeed.
4. ❌→🔄 **Slot update failed at `res=-50`** with a plain monolithic `make flash`
   (no valid slot). **Fix identified: the `riotboot_dfu` two-stage install
   (Part C).** Verified at build/offset level — the app (with `usbus_dfu`
   auto-enabled for this board in its Makefile) produces `SLOT0_OFFSET=0x4000` /
   `SLOT1_OFFSET=0x71800` matching the `riotboot_dfu` bootloader, and the
   composite firmware links — but **not yet re-run on hardware**. Redo items 4–7
   after the Part C riotboot_dfu install.

Progress on the riotboot_dfu path (2026-07-21):

5. ✅ **Stage 1 works on hardware** — after `make -C bootloaders/riotboot_dfu ...
   flash`, `sudo dfu-util -l` lists the `1209:7d02` device with `alt=0
   "RIOT-OS Slot 0"` and `alt=1 "RIOT-OS Slot 1"`. Bring-up surfaced two gotchas,
   both fixed in Part C: dfu-util needs `sudo`/udev, and stage 2 needs a pinned
   `APP_VER` (epoch drift between the slot-building sub-make and the flashing
   parent make → `Could not open file ... slot0.<epoch>.bin`).
6. ✅ **Stage 2 works; `res=-50` gone** — slot 0 boots with a valid header
   (`Running from slot 0`, `start_addr 0x4400`, `Manifest seq_no:` now prints).
7. ❌→✅ **`res=-4` `offset does not match` — root-caused and fixed.** The update
   fetched, verified, and validated the seqnr, then failed the slot-offset
   condition. Cause: the board sets the Nordic-bootloader flash reservation
   (`ROM_OFFSET=0x1000`, `ROM_LEN=0xdf000`) **only** for `PROGRAMMER=nrfutil`.
   The `dfu-util` slot flash therefore used the CPU default `ROM_LEN=0x100000`
   (full 1 MB) → `SLOT1_OFFSET=0x82000`, while the default-`PROGRAMMER`
   `suit/publish` used `0xdf000` → manifest `SLOT1=0x71800`. Pinned down with a
   temporary debug shell command (dumping `riotboot_slot_numof`/offsets on-device
   — since removed, see the gotcha below) that confirmed the running firmware had
   `SLOT1_OFFSET=0x82000` vs the manifest's `0x71800`. **Fixed** by pinning
   `ROM_OFFSET ?= 0x1000` / `ROM_LEN ?= 0xdf000` for `nrf52840dongle` in the app
   `Makefile` (now `PROGRAMMER`-independent — publish and dfu-util flash both
   give `0x71800`).
8. ✅ **Full OTA verified end-to-end on hardware (2026-07-22).** After the
   geometry fix, reflash slot 0 + republish, then `suit/notify`: the `0x71800`
   offset component matched, vendor/class ID OK, `SUIT policy check OK`,
   `riotboot_flashwrite: initializing update to target slot 1`, **`suit: payload
   decrypted (108028 bytes)`** (firmware payload encryption works too),
   `Verified installed payload`, `suit_worker: update successful`, reboot, and
   `current_slot` → **`Running from slot 1`**. This was Ed25519 + plaintext
   manifest + encrypted payload.
9. ✅ **Full-PQ combo verified end-to-end on hardware (2026-07-22): ML-DSA-65
   signing + ML-KEM-768 manifest encryption + ML-KEM-768 payload encryption.**
   `SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768`: manifest
   fetched (4892 B) and decrypted (3748 B plaintext — the ~1144 B ML-KEM-768
   overhead matches exactly), ML-DSA-65 signature verified, sequence number and
   vendor/class ID validated, `SUIT policy check OK`, then the firmware payload
   itself (`suit: decrypting payload (header 1126 bytes)`) decrypted
   (124188 bytes) via ML-KEM-768, `Verified installed payload`,
   `suit_worker: update successful`, reboot, `current_slot` →
   **`Running from slot 1`**. This is the combo that does **not** fit the
   samr21-xpro at all (~3.5 KB short even manifest-only) — the dongle's 256 KB
   RAM is what makes a full post-quantum (signature + encryption) SUIT update
   demonstration possible. `res=-5` on seqnr replay also observed and correct
   (anti-rollback rejecting a re-fetch of an already-installed sequence number).
   Two bring-up gotchas found and fixed along the way (see the Gotchas section):
   the manifest-encryption `--key` path must match the `SUIT_KEY_DIR` used for
   flashing (a stale/different directory's `device_mlkem768.pem` produces
   `manifest decryption failed: -213`, FIPS 203 implicit rejection — silent
   wrong shared secret, not a parse error); and `suit/publish` needs
   `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` passed explicitly too (it also
   governs the payload's auto-encryption scheme, defaults back to x25519
   otherwise, producing `suit: recipient alg -25 != built-in -70768` on the
   payload).
10. ⬜ Remaining to exercise: ML-DSA-87 signing (samr21 can't build it at all;
    dongle RAM should easily fit it).

Open risks for the riotboot_dfu path: (a) the app is a 3-function USB composite
(CDC-ACM + CDC-ECM + DFU-runtime) — it **links** fine (verified), but confirm it
enumerates correctly on hardware (all three interfaces present); (b) the usual
WSL `cdc_ether` / `usbip` re-attach dance across the extra `1209:7d02`
enumeration. (An external SWD probe with `PROGRAMMER=jlink make ...
riotboot/flash` remains a fallback but is not needed.)

---

## Gotchas specific to this setup

- **DFU re-enumeration drops the WSL attachment.** Re-run `usbipd attach --wsl`
  after entering DFU *and* after each `nrfutil` flash. #1 cause of "could not
  open port" / "device not found" here.
- **`PREFLASHER=true` when already in DFU.** Skips the 1200-baud reset touch
  that otherwise re-enumerates the bootloader mid-flash under WSL.
- **Networking is CDC-ECM, not ethos.** `make term` runs the CDC-ECM network
  script, **not** a serial console. Get the RIOT shell from the CDC-ACM serial
  (`/dev/ttyACM*`, `ID_MODEL_ID=7d01`) with `picocom`/`miniterm`. Don't use
  `dist/tools/ethos/setup_network.sh` on this board — ethos enumerates nothing.
- **Three USB identities.** Nordic DFU bootloader = `1209:7d00` (stage-1
  `nrfutil` flash); RIOT `riotboot_dfu` DFU mode = `1209:7d02` (stage-2 / slot
  re-flash with `dfu-util`); running RIOT = `1209:7d01` (CDC-ACM shell +
  CDC-ECM). Each re-enumeration drops the WSL `usbip` attachment — re-attach.
- **`usbus_dfu` is auto-enabled for this board** in the app Makefile (so slots
  align with `riotboot_dfu`). Do **not** try to add it via a make *argument*
  (`make USEMODULE+=usbus_dfu ...`) — GNU make treats that as an override and
  silently drops the app's own modules (libcose/suit), failing with
  `cose/sign.h: No such file`. It's already handled; you don't pass it at all.
- **Signing key must match the flashed key** — flash and `suit/publish` use the
  same `SUIT_KEY*`; re-flash if you switch keys or signing algorithm.
- **Pin `APP_VER` for `suit/publish` *and* the stage-2 `riotboot/flash-slot0`** —
  both name artifacts `...$(APP_VER)...` and evaluate `date +%s` in two make
  processes; without pinning, the flash looks for a `slot0.<epoch>.bin` that was
  never built (`dfu-util: Could not open file`).
- **`dfu-util` needs root.** Under WSL2 usbip, libusb can't claim the device as
  your user — `dfu-util -l` prints `Cannot open DFU device 1209:7d02`. Use
  `sudo dfu-util` (and `DFU="sudo dfu-util"` for the flash), or install the udev
  rule from Part C stage 1.
- **`'dfu-util' programmer is not supported by this board` is harmless** — it's
  not in `PROGRAMMERS_SUPPORTED`, but `riotboot/flash-slot0` calls it directly
  and the download runs anyway.
- **Flash geometry must not depend on `PROGRAMMER`.** The board only reserves the
  Nordic-bootloader space (`ROM_OFFSET=0x1000`, `ROM_LEN=0xdf000`) for
  `PROGRAMMER=nrfutil`; a `dfu-util` slot flash would otherwise use the full 1 MB
  (`ROM_LEN=0x100000`) and compute `SLOT1_OFFSET=0x82000` while `suit/publish`
  (default `nrfutil`) uses `0x71800` — the mismatch makes a fetched update fail
  the offset condition (`offset does not match`, `res=-4`). The app `Makefile`
  now pins `ROM_OFFSET`/`ROM_LEN` for the dongle so all `PROGRAMMER`s agree —
  confirmed fixed on hardware (Part G item 8). To sanity-check the geometry
  without touching firmware, compare the two build-time values directly:
  ```bash
  make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=dfu-util \
    info-debug-variable-SLOT1_OFFSET
  make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=nrfutil \
    info-debug-variable-SLOT1_OFFSET
  ```
  both must print `0x71800`. (A one-off on-device debug shell command was used
  to pin this down during bring-up; it was **removed** afterward — an
  unconditional `printf`-heavy shell command adds text/RAM to every board that
  builds this app, and it overflowed the RAM-constrained samr21-xpro's 32 KB
  budget. Don't re-add board-specific debug commands without an `#if
  BOARD_...`-style guard.)
- **Re-flashing a slot needs DFU mode.** Once slot 0 is running, the device is in
  DFU *runtime* (`1209:7d00`), not DFU mode (`1209:7d02`), and auto-detach can't
  bridge the PID gap. Force it: `sudo dfu-util -e -d 1209:7d00` (reboots into
  `riotboot_dfu` DFU mode), re-attach WSL, then re-run the stage-2 flash.
- **Never disable IRQs around USB-CDC-ACM `printf`.** stdio is USB here; a
  multi-line print with interrupts disabled deadlocks the device (buffer fills,
  USB can't drain). The stock `current_slot` command gets away with one short
  line under `irq_disable()`; don't copy that pattern for anything longer.
- **The URL buffer is actually 128 bytes, not 64** (`makefiles/suit.inc.mk`
  exports `-DCONFIG_SOCK_URLPATH_MAXLEN=128` for every SUIT app, overriding the
  64-byte default in `sys/include/net/sock/config.h`). The 64-char figure
  quoted elsewhere (e.g. SAMR21_EXAMPLES.md) describes the *convention*, not a
  hard limit — the full `coap://[2001:db8::1]/fw/suit_update/nrf52840dongle/
  riot.suit.enc` path (64 chars, longer than samr21's due to the longer board
  name) was fetched and processed correctly on hardware with no truncation.
  Keeping manifest names short (`riot.suit.enc`) is still good practice, just
  not load-bearing at this length.
- **`res=-5` on re-notify is expected** — sequence numbers must strictly
  increase; publish a newer image to update again.
- **`nrfutil` / the Nordic DFU is a one-time step.** It's used only in Part C
  stage 1 to install `riotboot_dfu`. After that, slot flashing goes through
  RIOT's own DFU (`dfu-util`, stage 2) and OTA updates go through `suit/notify`
  — neither touches the Nordic bootloader.
- **SUIT `suit/notify` updates need no re-attach.** They swap riotboot slots and
  keep the CDC-ECM link up (no DFU, no re-enumeration).
- **RESET button = Nordic DFU only.** Press it (slow red LED pulse) just to
  re-install the `riotboot_dfu` bootloader (stage 1). To re-flash a *slot* by
  wire you don't need it — the running firmware's DFU-runtime lets `dfu-util`
  trigger DFU mode itself.
- **RAM is not the constraint here.** Unlike the samr21, every crypto combo is
  expected to fit — if a build overflows, suspect a config mistake, not the
  board.
- **ML-KEM manifest-encrypt `--key` must match the flash's `SUIT_KEY_DIR`,
  exactly.** Each key directory has its own `device_mlkem768.pem` (a distinct
  keypair, not shared across directories like `ed25519-keys/` vs
  `mldsa-keys/`). Encrypting against the wrong directory's key produces
  `suit: manifest decryption failed: -213` (wolfSSL `MAC_CMP_FAILED_E`) with
  **no** earlier "CEK derivation failed" or "recipient alg" message — FIPS 203
  implicit rejection means a wrong KEM key silently yields a different (wrong)
  shared secret rather than an explicit decapsulation error, so the failure
  only surfaces at the final AEAD tag check.
- **`suit/publish` needs `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` passed
  explicitly too, not just on the flash.** It also selects the scheme
  `suit/publish` uses to auto-encrypt the **firmware payload**
  (`suit_firmware_encrypt` reuses the manifest-encryption device key/scheme).
  Omit it on publish and the payload gets encrypted for `device_x25519.pem`
  instead, even though the flashed firmware expects ML-KEM-768 — surfacing
  much later in the update, as `suit: recipient alg -25 != built-in -70768`
  right after `riotboot_flashwrite: initializing update to target slot 1`
  (the manifest itself decrypts fine, since that step doesn't depend on this
  flag being set at publish time — only the payload does).
- **Not security-audited.** RIOT's SUIT implementation is a reference, not a
  production-hardened stack.
