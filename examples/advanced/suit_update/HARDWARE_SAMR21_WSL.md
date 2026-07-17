# SUIT Update — SAMR21-xpro over WSL2

Recipe for running the SUIT firmware update flow on a real **Atmel/Microchip
SAMR21-xpro** board, driven from **WSL2** (Ubuntu). Uses the wired `ethos`
(Ethernet-over-serial) transport, which is the simplest reliable setup.

WSL2 can't see USB devices by default, so the first section attaches the
board's onboard debugger to WSL with `usbipd-win`. The rest mirrors the
standard hardware workflow, pinned to `samr21-xpro`.

Everything except the `usbipd` commands in Part A runs in the **WSL shell**.
The `usbipd` commands run in **Windows PowerShell as Administrator**.

---

## Part A — Connect the board to WSL2

The SAMR21-xpro exposes an onboard **EDBG (CMSIS-DAP)** debugger that provides
both the flash/debug interface and a USB-serial UART bridge over a single
micro-USB port.

1. Plug the USB cable into the port labelled **EDBG USB** (the one nearest the
   corner, not the "TARGET USB" port).

2. Install `usbipd-win` on Windows (one-time). In an **admin PowerShell**:
   ```powershell
   winget install --exact dorssel.usbipd-win
   ```
   Close and reopen the PowerShell afterwards so `usbipd` is on PATH.

3. Make sure the USB/IP client tools are present in WSL (one-time):
   ```bash
   sudo apt-get update
   sudo apt-get install -y usbip hwdata usbutils
   ```

4. List USB devices and find the board. In **admin PowerShell**:
   ```powershell
   usbipd list
   ```
   Look for a device from **Atmel Corp. / Microchip** (EDBG). Note its
   `BUSID` (e.g. `2-4`).

5. Bind it (one-time per device) and attach it to WSL:
   ```powershell
   usbipd bind   --busid 2-4
   usbipd attach --wsl --busid 2-4
   ```
   `bind` needs admin; `attach` you can re-run (non-admin) after each replug or
   reboot. Keep this PowerShell open — detaching or unplugging removes the
   device from WSL.

6. Confirm WSL now sees it. In the **WSL shell**:
   ```bash
   lsusb                 # should list Atmel Corp. EDBG CMSIS-DAP
   ls /dev/ttyACM*       # the UART bridge, usually /dev/ttyACM0
   ```
   If `/dev/ttyACM0` is missing but `lsusb` shows the board, replug and
   re-`attach`, then check `dmesg | tail`.

> **Troubleshooting:** if a flash later fails, the first thing to check is
> whether the device is still attached (`lsusb` in WSL). A dropped USB/IP
> attachment looks exactly like a code/flash bug but isn't one.

---

## Part B — Prerequisites (one-time, in WSL)

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
```

You also need the ARM toolchain to build for the board:

```bash
sudo apt-get install -y gcc-arm-none-eabi
```

RIOT builds its own copy of the `edbg` flasher automatically on first flash, so
nothing else is required to program the board. It links against `libudev`, which
is already present on Ubuntu; if the build ever complains, install it with
`sudo apt-get install -y libudev-dev`.

> **First-flash race:** the very first `make flash` may abort while fetching the
> `edbg` source with `make -C .../edbg/bin: No such file or directory. Stop.`
> This is a one-time checkout race — just run the same `make flash` command
> again and it builds `edbg` and flashes normally.

---

## Part C — Set up the ethos network

`ethos` bridges the board's serial line to a `tap` interface on the host, and
`uhcpd` serves the IPv6 prefix over it. Build both host tools once (otherwise
`setup_network.sh` aborts with `uhcpd: not found`):

```bash
cd RIOTBASE
make -C dist/tools/ethos
make -C dist/tools/uhcpd
```

Then, in a dedicated WSL shell (keep it running):

```bash
sudo dist/tools/ethos/setup_network.sh riot0 2001:db8::/64
```

This creates the `riot0` tap interface and serves the `2001:db8::/64` prefix.
Leave it open for the whole session.

> Exit the `make term` session (Part E) **before** stopping this, or the
> `riot0` interface won't be cleaned up properly.

In another shell, add a routable address on the host side of `riot0`:

```bash
sudo ip address add 2001:db8::1/128 dev riot0
```

---

## Part D — Signing key, then build, flash, and run

### Pick a signing key (ed25519)

The public key that verifies manifests is **baked into the firmware at flash
time**, so it must match the key you later sign updates with. `suit-tool` only
supports ed25519 keys. If `SUIT_KEY_DIR`'s `default.pem` has been regenerated as
a non-ed25519 key elsewhere (e.g. ML-DSA post-quantum experiments in this repo),
both flashing and signing must use a dedicated ed25519 key instead — otherwise
`suit/publish` fails with `suit_tool.sign - Non-library key type not
implemented`, and even if signing worked the on-device signature check would
fail against a mismatched embedded key.

Generate one once (reused for both flash and publish):

```bash
mkdir -p keys
dist/tools/suit/gen_key.py keys/ed25519.pem   # skip if it already exists
```

Then export these for every `make` command in Parts D and F:

```bash
export SUIT_KEY_DIR=$(pwd)/keys
export SUIT_KEY=ed25519
```

### Flash

```bash
BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

A successful flash ends with `Done flashing`, and the debugger is detected as
`ATMEL EDBG CMSIS-DAP ... Target: SAM R21G18`. A single
`verification failed ... at address 0x1004` line *before* the `Programming`
step is normal — that's `edbg` comparing the old flash contents; the real
check is the `Verification.... done.` line after programming.

### Run

`ethos` provides the board's interactive shell over the same serial link it
uses for networking, so you interact with the board through the terminal
started by `setup_network.sh` in Part C — you do **not** run a separate
`make term` (that would fight ethos for `/dev/ttyACM0`).

The board auto-configures the stable link-local address **`fe80::2`** on its
ethos interface, so `SUIT_CLIENT=[fe80::2%riot0]`. Confirm it's reachable from
the host:

```bash
ping -c3 fe80::2%riot0
```

---

## Part E — Start the CoAP file server

In its own shell, hosting the `coaproot` directory (keep running):

```bash
cd RIOTBASE
mkdir -p coaproot
aiocoap-fileserver coaproot
```

---

## Part F — Publish and notify an update

Unlike the native example, on hardware you use the `suit/publish` and
`suit/notify` Make targets — they build a new signed firmware image, copy it
into `coaproot`, and tell the device to fetch it. Make sure the `SUIT_KEY_DIR`
/ `SUIT_KEY` exports from Part D are still set (same key as the flash).

> **Pin `APP_VER`.** The manifest sequence number defaults to `$(date +%s)`,
> which is evaluated independently by the parent make and the riotboot
> sub-make. On a real board those two invocations can land a few seconds apart,
> and the manifest generator then looks for a slot binary whose timestamp
> doesn't exist, failing with `FileNotFoundError: ... slot0.<epoch>.bin`. Pin it
> to one value for the whole publish by capturing the epoch first:

1. Build and publish a new firmware image:
   ```bash
   APP_VER=$(date +%s)   # one value for both make passes
   BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
     make -C examples/advanced/suit_update suit/publish
   ```
   You should see pairs of `published … as coap://[2001:db8::1]/fw/...` lines.

2. Notify the device:
   ```bash
   SUIT_COAP_SERVER=[2001:db8::1] \
   SUIT_CLIENT=[fe80::2%riot0] \
   BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
   ```

On the serial terminal the device fetches and verifies the manifest, downloads
the new image into the inactive slot, then reboots into it (note the
`Running from slot 1` line after the reboot):

```
suit_worker: got manifest with size 485
suit: verifying manifest signature   (hangs a couple seconds here)
suit: validated manifest version
Manifest seq_no: <epoch>, highest available: <older>
...
Fetching firmware |█████████████████████████| 100%
...
```

To roll out another update, re-run Part F with a fresh `APP_VER=$(date +%s)`.
Because the sequence number must strictly increase, re-notifying with the *same*
published manifest is correctly rejected by the device:

```
Manifest seq_no: 1784305079, highest available: 1784305079
seq_nr <= running image
suit_worker: suit_parse() failed. res=-5
suit_worker: update failed, hdr invalid
```

That `res=-5` is the anti-rollback check doing its job, not a bug — publish a
newer image (higher `APP_VER`) to update again.

---

## Gotchas specific to this setup

- **USB passthrough drops on replug/reboot.** `usbipd attach` must be re-run
  after unplugging the board or restarting Windows/WSL. `bind` persists.
- **Signing key must match the flashed key.** The verifying public key is baked
  into the firmware at flash time; flash and `suit/publish` must use the same
  `SUIT_KEY`. Re-flash if you switch keys (see Part D).
- **Pin `APP_VER` for `suit/publish`** to avoid the epoch-drift
  `FileNotFoundError` (see Part F).
- **`res=-5` on re-notify is expected** — the sequence number must increase;
  publish a newer image to update again.
- **Don't run `make term` under ethos.** The `setup_network.sh` (ethos) shell
  *is* the board terminal; a second `make term` fights it for `/dev/ttyACM0`.
- **`riot0` cleanup.** Always stop the board terminal before killing the
  `setup_network.sh` shell, or the `riot0` interface leaks.
- **Which USB port.** Use the EDBG port, not the target port — only the EDBG
  side carries the debugger + UART.
- **Wireless (802.15.4/BLE) instead of ethos** is possible but needs a border
  router; see [README.hardware.md](README.hardware.md) for that path.
- **Not security-audited.** RIOT's SUIT implementation is a reference, not a
  production-hardened stack.
