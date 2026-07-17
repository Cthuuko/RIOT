# SUIT Update — nRF52840 Dongle over WSL2

Recipe for running the SUIT firmware update flow on a **Nordic nRF52840 Dongle**
(`BOARD=nrf52840dongle`), driven from **WSL2** (Ubuntu), using the wired `ethos`
transport over the dongle's USB-serial link.

This board is meaningfully more awkward under WSL than a board with an onboard
debugger (like the samr21-xpro), because it is flashed through its **USB DFU
bootloader** with `nrfutil` — and **every mode change re-enumerates the USB
device**, which drops the WSL `usbip` attachment. You will re-attach the device
from Windows several times. That is expected, not a fault.

> If you also have the samr21-xpro guide, most of Parts B–F are identical; the
> real differences are the DFU flashing in Part D and the re-attach dance.

The dongle has **no separate debug UART**: the single USB port is the DFU
bootloader in one mode and RIOT's CDC-ACM serial in the other. So the same
physical port is used for flashing *and* for ethos, just at different times.

---

## Part A — Connect the dongle to WSL2

The dongle enumerates two different ways:

| State | USB ID | Linux node | Used for |
|---|---|---|---|
| DFU bootloader | `1209:7d00` | `/dev/ttyACM*` | flashing with `nrfutil` |
| Running RIOT | `1209:7d01`* | `/dev/ttyACM*` | ethos / serial |

\*RIOT's CDC-ACM PID; the exact value may differ, but it is a *different* device
from the bootloader, so WSL treats it as a new attachment each time.

1. Install `usbipd-win` on Windows and the USB/IP client in WSL (one-time) — see
   Part A of the samr21 guide if you haven't. Confirm WSL sees USB devices with
   `lsusb`.

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
   The dongle shows `ID_MODEL_ID=7d00`. Use that node as `PORT` below (this
   guide assumes `/dev/ttyACM0`; adjust if yours differs).

> **You will repeat step 3 (`usbipd attach`) after every flash**, because the
> dongle re-enumerates when it switches between the bootloader and RIOT. `bind`
> only needs doing once.

---

## Part B — Prerequisites (one-time, in WSL)

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
sudo apt-get install -y gcc-arm-none-eabi
```

You also need Nordic's `nrfutil` on PATH (the flasher). Verify:

```bash
nrfutil version    # or: which nrfutil
```

If missing, install it from Nordic (the classic Python `nrfutil`, which provides
the `dfu` command RIOT uses), e.g. `pip3 install --user nrfutil`.

---

## Part C — Set up the ethos network

Build the host tools once and start the network (needs the ARM/host toolchain):

```bash
cd RIOTBASE
make -C dist/tools/ethos
make -C dist/tools/uhcpd
sudo dist/tools/ethos/setup_network.sh riot0 2001:db8::/64
```

Leave that shell running, and in another shell add a host address:

```bash
sudo ip address add 2001:db8::1/128 dev riot0
```

> `ethos` needs the dongle running **RIOT** (not the bootloader), so it only
> works *after* Part D's flash. If you start it while the dongle is still in DFU,
> it will just wait — that's fine.

---

## Part D — Signing key, then flash via DFU

### Signing key (ed25519)

The verifying public key is baked into the firmware at flash time and must match
the key you later sign updates with. `suit-tool` only supports ed25519, so if
`default.pem` in `SUIT_KEY_DIR` was regenerated as a non-ed25519 key (e.g. an
ML-DSA post-quantum key), use a dedicated ed25519 key for both flashing and
publishing:

```bash
mkdir -p keys
dist/tools/suit/gen_key.py keys/ed25519.pem    # skip if it already exists
export SUIT_KEY_DIR=$(pwd)/keys
export SUIT_KEY=ed25519
```

### Flash

With the dongle in **DFU mode and attached to WSL** (Part A), flash it. The
crucial WSL-specific flag is **`PREFLASHER=true`**:

```bash
BOARD=nrf52840dongle PORT=/dev/ttyACM0 PREFLASHER=true \
  make -C examples/advanced/suit_update flash
```

Why `PREFLASHER=true`: RIOT's normal flash sequence first "touches" the serial
port at 1200 baud to kick a *running RIOT app* into the bootloader. But when the
dongle is **already in DFU**, that touch instead resets the bootloader and
re-enumerates the USB device — which under WSL drops the `usbip` attachment, so
`nrfutil` then fails with `could not open port /dev/ttyACM0: No such file or
directory`. `PREFLASHER=true` replaces that touch with a no-op.

`nrfutil` builds a DFU `.zip` package and programs it over serial; a successful
run ends with the DFU transfer completing and the dongle rebooting into RIOT.

### Re-attach after flashing

The moment the dongle reboots into RIOT it re-enumerates (now `1209:7d01`) and
**drops off WSL**. Re-attach it from Windows:

```powershell
usbipd attach --wsl --busid <BUSID>
```

Then confirm RIOT is up and reachable over ethos (the dongle auto-configures the
stable link-local address `fe80::2` on its ethos interface):

```bash
ls /dev/ttyACM*            # the RIOT CDC-ACM serial should be back
ping -c3 fe80::2%riot0     # needs the Part C network running
```

So `SUIT_CLIENT=[fe80::2%riot0]`.

---

## Part E — Start the CoAP file server

```bash
cd RIOTBASE
mkdir -p coaproot
aiocoap-fileserver coaproot
```

---

## Part F — Publish and notify an update

Keep the `SUIT_KEY_DIR` / `SUIT_KEY` exports from Part D set (same key as the
flash).

> **Pin `APP_VER`.** The manifest sequence number defaults to `$(date +%s)`,
> evaluated independently by the parent make and the riotboot sub-make; a few
> seconds' drift makes the manifest generator look for a slot binary that
> doesn't exist (`FileNotFoundError: ... slot0.<epoch>.bin`). Pin it once:

1. Build and publish:
   ```bash
   APP_VER=$(date +%s)
   BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
     make -C examples/advanced/suit_update suit/publish
   ```

2. Notify the device:
   ```bash
   SUIT_COAP_SERVER=[2001:db8::1] \
   SUIT_CLIENT=[fe80::2%riot0] \
   BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
   ```

The dongle fetches and verifies the manifest, writes the new image to the
inactive riotboot slot, and reboots into it (`Running from slot 1`).

> **Note:** a SUIT update writes the new firmware to the *other riotboot slot*
> and boots it directly — it does **not** go back through the DFU bootloader. So
> unlike the initial `nrfutil` flash, a `suit/notify` update does **not** need a
> WSL re-attach; the ethos link stays up across the slot switch.

To update again, re-run Part F with a fresh `APP_VER=$(date +%s)`. Re-sending an
already-installed sequence number is correctly rejected:

```
Manifest seq_no: <n>, highest available: <n>
seq_nr <= running image
suit_worker: suit_parse() failed. res=-5
```

That `res=-5` is the anti-rollback check working, not a bug.

---

## Gotchas specific to this setup

- **DFU re-enumeration drops the WSL attachment.** Re-run `usbipd attach --wsl`
  after entering DFU *and* after each `nrfutil` flash. This is the #1 cause of
  "could not open port" and "device not found" errors here.
- **`PREFLASHER=true` when already in DFU.** Skips the 1200-baud reset touch
  that otherwise re-enumerates the bootloader mid-flash under WSL.
- **Signing key must match the flashed key** — flash and `suit/publish` use the
  same `SUIT_KEY`; re-flash if you switch keys.
- **Pin `APP_VER` for `suit/publish`** to avoid the epoch-drift
  `FileNotFoundError`.
- **`res=-5` on re-notify is expected** — sequence numbers must strictly
  increase; publish a newer image to update again.
- **Don't run `make term` under ethos** — the `setup_network.sh` (ethos) shell
  is the board terminal; a second `make term` fights it for the serial port.
- **SUIT updates don't use DFU.** Only the *initial* provisioning uses
  `nrfutil`; subsequent `suit/notify` updates swap riotboot slots and keep the
  ethos link, so they need no re-attach.
- **Enter DFU with the RESET button** (slow red LED pulse) whenever you need to
  re-flash with `nrfutil` from scratch.
- **Not security-audited.** RIOT's SUIT implementation is a reference, not a
  production-hardened stack.
