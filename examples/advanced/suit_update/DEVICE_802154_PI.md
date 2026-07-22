# Mesh mode — wireless SUIT updates over 802.15.4, served from a Raspberry Pi

Both real boards as **untethered 802.15.4 nodes**, a **Raspberry Pi 4** as the
CoAP file server and the thing that triggers updates, and an **openlabs
KW41Z-mini** as the 6LoWPAN border router hanging off the Pi's UART over SLIP.

This is the realistic OTA topology. The other two hardware guides
([DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md),
[DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md)) are **tethered mode** —
one board, wired to the build host, updates travelling over the same cable as
the shell. Everything about the SUIT workflow itself is identical; only the path
the CoAP packets take changes.

```
  build host (WSL2, or the Pi itself)
        │  artifacts (rsync)
        ▼
  Raspberry Pi 4 ── UART/SLIP ── KW41Z-mini ))) 802.15.4 (((── samr21-xpro     (node)
   aiocoap-fileserver             border router             ── nRF52840 dongle (node)
   + suit/notify                  2001:db8::/64 via uhcpd
   fdea:dbee:f::1 on sl0
```

**Prerequisite:** [SETUP_COMMON.md](SETUP_COMMON.md) §1 (host tools) and §3 (a
signing key), on whichever machine you build on. Concepts: [GUIDE.md](GUIDE.md).

---

## What is different in this topology

| | |
|---|---|
| **Update transport** | 802.15.4 / 6LoWPAN — no cable carries CoAP |
| **CoAP server** | on the Pi, not the build host |
| **Trigger** | `suit/notify` runs **on the Pi** — it is the only host with a route into the mesh |
| **`SUIT_COAP_SERVER`** | `[fdea:dbee:f::1]` — the Pi's address *on the SLIP link* |
| **`SUIT_CLIENT`** | each node's global `2001:db8::<eui64>`, read from its own console |
| **Node addressing** | from the BR's router advertisements (no `gnrc_uhcpc` on the nodes) |
| **Still tethered** | only the **consoles**: samr21 over EDBG UART, dongle over CDC-ACM |

The crypto axes and the matching rule are completely unchanged — see
[SETUP_COMMON.md](SETUP_COMMON.md) §2. Per-board feasibility (which PQ
combinations fit) also still lives in each board's own guide.

---

## Part A — The Raspberry Pi

### A.1 Which image

**Raspberry Pi OS Lite (64-bit), Bookworm.** Lite because this is a headless
testbed server; 64-bit because Debian arm64 has both `gcc-arm-none-eabi` and
`openocd`, which keeps "build on the Pi" possible.

Two Bookworm consequences, both handled below: it enforces PEP 668 (so
`pip3 install --user` refuses to run) and it ships **OpenOCD 0.12**, which
renamed the `bcm2835gpio_*` commands that RIOT's
`makefiles/tools/openocd-adapters/raspi.inc.mk` still emits.

> If you would rather not touch OpenOCD config at all, Raspberry Pi OS
> **Bullseye** (64-bit, Lite) ships OpenOCD **0.11** — the syntax `raspi.inc.mk`
> was written against — and predates PEP 668. The cost is running a release past
> its main support window. Start on Bookworm; fall back only if the OpenOCD
> override becomes a time sink. Check `openocd --version` either way.

### A.2 Getting in, headless

SSH must be enabled **before first boot** — Bookworm has no default `pi` user
and no SSH server running.

In Raspberry Pi Imager, open "Edit settings" before writing and set: hostname
(e.g. `suitpi`), username + password, **Enable SSH**, and Wi-Fi SSID/password
*plus the Wi-Fi country* (the radio stays off without it). Then `ssh
<user>@suitpi.local`.

Already flashed without that? Re-mount the card's FAT `bootfs` partition (it
mounts on Windows too) and add:

- an empty file named `ssh` — starts the SSH server on next boot
- `userconf.txt`, one line `username:<hash>` from `openssl passwd -6` — without
  it there is no account to log into

The old `wpa_supplicant.conf`-on-`bootfs` trick **no longer works** on Bookworm
(networking moved to NetworkManager). Use **Ethernet for the first boot** and
`nmcli device wifi connect <ssid> password <pw>` afterwards — Ethernet is the
better choice for this testbed anyway.

`*.local` resolution often fails from inside WSL2. If it does, take the IP from
your router or `nmap -sn`, and SSH to that.

### A.3 Free up the good UART

On a Pi 4, `/dev/serial0` is the **mini-UART** (`ttyS0`) by default, because the
PL011 (`ttyAMA0`) is claimed by Bluetooth. The mini-UART derives its baud rate
from the VPU core clock, so it drifts under frequency scaling — a textbook cause
of intermittent SLIP corruption that looks exactly like a flaky radio.

In `/boot/firmware/config.txt` (`/boot/config.txt` on older images):

```
enable_uart=1
dtoverlay=disable-bt
```

then:

```bash
sudo systemctl disable --now hciuart
# BOTH gettys: which one is live depends on where serial0 points, and
# disable-bt moves it from ttyS0 to ttyAMA0
sudo systemctl disable --now serial-getty@ttyAMA0.service
sudo systemctl disable --now serial-getty@ttyS0.service
# remove console=serial0,115200 (and console=ttyAMA0,...) from
# /boot/firmware/cmdline.txt
sudo reboot
```

Verify afterwards that `ls -l /dev/serial0` resolves to `ttyAMA0`, and that
nothing else holds the port:

```bash
sudo fuser -v /dev/serial0     # should report no process before you start sliptty
grep -o 'console=[^ ]*' /boot/firmware/cmdline.txt
```

> **Why this matters more than it looks.** Anything else reading that port
> steals bytes from `sliptty`, which then loses SLIP frame sync and prints
> `Unknown packet type 0x??` followed by binary garbage — and, before that, the
> border router's shell simply appears not to answer, because the getty consumed
> its output. It is easy to misread as a dead board or a wiring fault.

### A.4 Packages

```bash
sudo apt-get update
sudo apt-get install -y build-essential git openocd picocom rsync \
                        gcc-arm-none-eabi
```

**`gcc-arm-none-eabi` is not optional here**, even if you build the node
firmware on your dev machine: the border router (Part C) is compiled *and*
flashed on the Pi, because the Pi's own GPIO header is the SWD probe.

### A.5 A RIOT checkout on the Pi

Parts C and D both run `make` from a RIOT tree, so the Pi needs one:

```bash
git clone https://github.com/RIOT-OS/RIOT.git ~/RIOT     # or your fork
cd ~/RIOT
```

Every `make -C ...` command in this guide is relative to that checkout — `cd`
there first, or use absolute paths.

Two things to know about which tree to clone:

- **The border router needs nothing from this thesis.**
  `examples/networking/gnrc/border_router` is stock upstream RIOT, so a plain
  clone works, and the Pi's tree does *not* have to match your dev machine's
  version for the mesh to come up.
- **Only clone your fork if you will also build node firmware on the Pi**
  (Part F.2). That path additionally needs the signing keys and, for the PQ
  variants, the ~1.1 GB wolfSSL checkout — see F.2 before committing to it. If
  you build on the dev host and `rsync` the artifacts (F.1, recommended), the
  Pi's tree is only ever used for the border router and the host tools.

Then build the two host helpers:

```bash
make -C dist/tools/uhcpd
make -C dist/tools/sliptty
```

### A.6 Python — the CoAP file server

**Pi OS Lite ships no pip.** `pip3: command not found` is expected on a fresh
image, not a PATH problem:

```bash
sudo apt-get install -y python3-pip python3-venv
```

`aiocoap` must come from pip either way — the `linkheader` extra pulls
`LinkHeader`, which Debian does not package. Bookworm then enforces PEP 668, so
a bare `pip3 install --user` fails with `externally-managed-environment`. Use a
venv:

```bash
python3 -m venv ~/.venvs/suit
~/.venvs/suit/bin/pip install 'aiocoap[linkheader]>=0.4.1'
echo 'export PATH=$HOME/.venvs/suit/bin:$PATH' >> ~/.bashrc && . ~/.bashrc
which aiocoap-fileserver     # must print a path
```

Or, if you would rather not manage a venv,
`pip3 install --break-system-packages 'aiocoap[linkheader]>=0.4.1'` — same
result, at the cost of writing into the apt-managed site-packages.

Either way, **check `which aiocoap-fileserver` prints a path** before moving
on; if it does not, Part D will look like it silently did nothing.

Only needed if you will also *build and sign* on the Pi (Part F.2) — `aiocoap`
alone covers serving and notifying:

```bash
sudo apt-get install -y python3-cbor2 python3-cryptography
```

---

## Part B — Wire the KW41Z-mini to the Pi header

Seven wires: two power, two UART, three SWD. **Physical header pin numbers** on
the left (pin 1 is the corner nearest the SD-card end):

| Pi 4 pin | Pi signal | dir | KW41Z-mini | Purpose |
|---|---|---|---|---|
| 1  | 3V3          | →  | 3.3V   | power |
| 6  | GND          | —  | GND    | ground (any of 6/9/14/20/25/30/34/39) |
| 8  | GPIO14 / TXD | →  | RXI    | SLIP + BR console |
| 10 | GPIO15 / RXD | ←  | TXO    | SLIP + BR console |
| 36 | GPIO16       | →  | RST    | SWD reset (`SRST_PIN`) |
| 38 | GPIO20       | →  | SWDCLK | SWD clock (`SWCLK_PIN`) |
| 40 | GPIO21       | ↔  | SWDIO  | SWD data (`SWDIO_PIN`) |

- **The UART is cross-wired** (TX→RX, RX→TX) and carries *both* the SLIP link
  and the border router's shell on one line. That is by design:
  `UPLINK=slip` selects `slipdev_stdio`
  (`examples/networking/gnrc/border_router/Makefile.board.dep`), which points
  `SLIPDEV_PARAM_UART` at `STDIO_UART_DEV`. The KW41Z-mini has exactly one UART
  (LPUART0, RX = PTC6, TX = PTC7), so there is nothing to separate them onto;
  `sliptty` demultiplexes on the Pi side.
- **SWD numbers above are BCM GPIO numbers, not header positions** — the header
  column is the physical pin that carries them.
- **These are the in-tree defaults**, so no pin variables need to be passed to
  `make`. Two inconsistencies to be aware of, so a wiring problem is not
  mistaken for a dead board:
  - `boards/openlabs-kw41z-mini/doc.md` draws **RST on GPIO19**; its
    `Makefile.include` sets `SRST_PIN = 16`. The Makefile is what reaches
    OpenOCD — wire GPIO16 (pin 36). If you already wired GPIO19, pass
    `SRST_PIN=19`.
  - `raspi.inc.mk` defaults SWCLK/SWDIO the **other way round** (21/20) from the
    board file (20/21). **The board file wins** — verified, not assumed:
    `make info-debug-variable-OPENOCD_ADAPTER_INIT` yields
    `bcm2835gpio_swd_nums 20 21`, i.e. SWCLK = GPIO20, SWDIO = GPIO21.
- **These three pins are also SPI1** (GPIO16 = CE2, GPIO20 = MOSI, GPIO21 =
  SCLK). Do not enable SPI1 in `config.txt`, or they will be claimed out from
  under the bit-banged SWD. Check with `raspi-gpio get 16,20,21` — you want
  `func=INPUT`, not `ALT4`.
- **The pins are relocatable** if this layout is awkward: `SRST_PIN`,
  `SWCLK_PIN` and `SWDIO_PIN` on the `make` command line override both the board
  file and `raspi.inc.mk`. If you move them, prefer pins without fixed pull-ups
  for SWDIO — GPIO2/GPIO3 carry non-disableable 1.8 kΩ pull-ups (they are the
  I²C pins), which suit an active-low SRST or a Pi-driven SWCLK but not the
  bidirectional data line.
- Power from **3V3 (pin 1)**, not 5V — and do not forget the ground wire; SWD
  fails with `cannot read IDR` without it.

---

## Part C — Flash and run the border router

**Do this before anything else is wired up.** It is the highest-risk step, and a
failure here is unambiguous, whereas a failure later is not.

```bash
DEFAULT_CHANNEL=26 DEFAULT_PAN_ID=0x23 \
  UPLINK=slip PREFIX_CONF=uhcp IPV6_PREFIX=2001:db8::/64 SLIP_BAUDRATE=115200 \
  OPENOCD_DEBUG_ADAPTER=raspi \
  BOARD=openlabs-kw41z-mini make -C examples/networking/gnrc/border_router flash
```

No pin variables are needed — the Part B wiring is the board file's default
(GPIO16/20/21). If you relocated the SWD lines, add `SRST_PIN=` / `SWCLK_PIN=` /
`SWDIO_PIN=` here **and** on `make reset`, which goes through the same OpenOCD
path.

`OPENOCD_DEBUG_ADAPTER=raspi` overrides the board's `sysfs_gpio` default —
OpenOCD dropped the `sysfsgpio` driver in 0.12, which is what Bookworm ships.
`raspi.inc.mk` already detects the Pi 4 (`PERIPH_BASE = 0xFE000000`) and exports
`OPENOCD ?= sudo -E openocd`, since `bcm2835gpio` needs `/dev/mem`.

> **`DEPRECATED! use 'bcm2835gpio peripheral_base' not
> 'bcm2835gpio_peripheral_base'` is harmless.** OpenOCD 0.12 renamed the
> `bcm2835gpio_*` commands that `raspi.inc.mk` still emits, but it still accepts
> the old spellings and carries on. Confirmed on 0.12.0 with Pi 4 detection
> working (`peripheral_base = 0xfe000000`). Ignore the warnings; no override and
> no older OpenOCD is needed. (The forked OpenOCD suggested in
> `boards/openlabs-kw41z-mini/doc.md` is from 2019 — that advice is stale.)

A successful run is unmistakable — the debug port answers, the part is
identified, and the write is verified:

```
Info : SWD DPIDR 0x0bc11477
Info : [klx.cpu] Cortex-M0+ r0p1 processor detected
Info : Kinetis MKW41Z512xxx4 detected: 2 flash blocks
wrote 98304 bytes from file .../gnrc_border_router.elf in 2.333400s
verified 97080 bytes in 0.388454s
Done flashing
```

Two benign lines on the way there: `FOPT requested in the programmed file
differs from current setting` / `Trying to re-program FCF` (OpenOCD fixing up
the Kinetis flash configuration field), and `Disabling Kinetis watchdog`.
Neither needs action.

### If it fails with `Error connecting DP: cannot read IDR`

The debug port never answered — usually **electrical**, not configuration.
First confirm what OpenOCD is actually driving:

```bash
make -C examples/networking/gnrc/border_router info-debug-variable-OPENOCD_ADAPTER_INIT \
  OPENOCD_DEBUG_ADAPTER=raspi BOARD=openlabs-kw41z-mini
# expect: ... bcm2835gpio_swd_nums 20 21 ... bcm2835gpio_srst_num 16 ...
```

`swd_nums` is `<SWCLK> <SWDIO>`, in that order. The flash log also echoes the
reset pin (`adapter gpio srst (output): num 16`) — a quick way to catch a stale
or forgotten override. Then work through:

- **Pins claimed by another function.** `raspi-gpio get 16,20,21` — they must
  show `func=INPUT`, not `ALT4` (that is SPI1: CE2/MOSI/SCLK).
- **Ground.** The module needs both 3V3 (pin 1) **and** a shared GND (pin 6). A
  missing ground produces exactly this error.
- **BCM number vs. header position.** GPIO16 = physical pin **36**, GPIO20 =
  pin **38**, GPIO21 = pin **40**. Easy to be one row off.
- **RST on the wrong pin.** If you wired from `doc.md`'s diagram (GPIO19, pin
  35) rather than this guide's table, either move the wire to pin 36 or pass
  `SRST_PIN=19`.
- **Clock too fast for dupont leads.** The default is ~1 MHz; drop it:
  ```bash
  OPENOCD_DEBUG_ADAPTER=raspi BOARD=openlabs-kw41z-mini \
    OPENOCD_EXTRA_INIT="-c 'adapter speed 100'" \
    make -C examples/networking/gnrc/border_router flash
  ```

> **Use the identical variable set on `flash` and on `term`.** `UPLINK` defaults
> to `ethos`, so a `make flash` that forgot `UPLINK=slip` produces an *ethos*
> border router — and `sliptty` then talks SLIP to a board speaking ethos, which
> fails **silently**: the link comes up, the shell simply never answers. Same
> class of trap as the SUIT matching rule in
> [SETUP_COMMON.md](SETUP_COMMON.md) §2.1. Export the set once and reuse it:
>
> ```bash
> export BR_FLAGS="UPLINK=slip PREFIX_CONF=uhcp IPV6_PREFIX=2001:db8::/64 \
>                  SLIP_BAUDRATE=115200 BOARD=openlabs-kw41z-mini"
> env $BR_FLAGS OPENOCD_DEBUG_ADAPTER=raspi \
>   make -C examples/networking/gnrc/border_router flash
> ```

Then, in a **dedicated terminal that stays open for the whole session**:

```bash
UPLINK=slip PREFIX_CONF=uhcp IPV6_PREFIX=2001:db8::/64 SLIP_BAUDRATE=115200 \
  BOARD=openlabs-kw41z-mini PORT=/dev/serial0 \
  make -C examples/networking/gnrc/border_router term
# equivalently:
#   sudo sh dist/tools/sliptty/start_network.sh 2001:db8::/64 /dev/serial0 115200
```

This creates the `sl0` tun (host `fe80::1`, device `fe80::2`), routes
`2001:db8::/64` into the mesh, and starts `uhcpd` to delegate the prefix.
`Device "sl0" does not exist` early in the output is a benign pre-check before
the interface is created; you want to see `Starting dispatch. TUN: …, Stream: …`.

> **`make term` may fail while building host tools** with
> `error: static declaration of 'explicit_bzero' follows non-static
> declaration` in `zep_dispatch`. That is a RIOT portability bug —
> `sys/include/string_utils.h` gates its own `explicit_bzero` on `CPU_NATIVE`,
> which is undefined for host-tool builds, so it collides with glibc's. It
> shows up on Bookworm/arm64 but not on Ubuntu 22.04/x86-64. `zep_dispatch` is a
> ZEP dispatcher for *simulated* radios and is not needed here; `TERMDEPS=`
> skips it, or run `start_network.sh` directly as above. (Fixed in this fork by
> adding `!defined(__GLIBC__)` to that guard.)

**This terminal is also the border router's shell.** Check it:

```
> ifconfig
```

You want two interfaces: the SLIP uplink, and an 802.15.4 one on channel 26 /
PAN 0x23 carrying a `2001:db8::` address.

> **Radio note.** `boards/openlabs-kw41z-mini/Makefile.dep` maps
> `netdev_default` to `kw41zrf`, the KW41Z's *integrated* radio. That is the
> right thing to use: same PHY as the nodes (2.4 GHz O-QPSK, 250 kbit/s,
> channels 11–26), no extra driver work. There is no AT86RF233 wiring in-tree
> for this board.
>
> Do not confuse it with openlabs' *other* product, the "Raspberry Pi 802.15.4
> radio" — a bare AT86RF233 on the Pi's SPI with no MCU and no SLIP, driven by
> the Linux `at86rf230` kernel driver. That would be a completely different
> setup, and would additionally need `CONFIG_GNRC_IPV6_NIB_SLAAC=1` on every
> node, because Linux does not implement 6LoWPAN-ND.

---

## Part D — The CoAP file server

Second terminal on the Pi, also kept running. **No `ip address add` is needed** —
`start_network.sh` already gave `sl0` the address the nodes will use:

```bash
mkdir -p ~/coaproot && aiocoap-fileserver ~/coaproot
```

Confirm it is there: `ip -6 addr show sl0` lists `fdea:dbee:f::1/64` alongside
`fe80::1` (`dist/tools/sliptty/start_network.sh:8,47` — the `TUN_GLB` default).

> ### Why the server is *not* `2001:db8::1`
>
> This is the single easiest thing to get wrong here, and it fails in a way that
> looks like a broken radio.
>
> `2001:db8::/64` is the prefix the border router **advertises into the mesh**.
> If you give the Pi an address inside it, every node treats that address as
> *on-link on the 802.15.4 interface*, does neighbour discovery for it over the
> radio, gets no answer, and never uses its default route — even though the
> route, the neighbour cache entry and the BR itself are all perfectly healthy.
> Symptom: `ping 2001:db8::1` from a node is 100 % loss while
> `nib route`/`nib neigh` look correct, and a SUIT fetch dies with
> `suit_worker: error getting manifest`.
>
> `fdea:dbee:f::1` sits **outside** the delegated prefix, so the node falls
> through to `default via <BR>` → the BR's own default route (`fe80::1`, added by
> `gnrc_uhcpc`) → the Pi. That is exactly why the SLIP script assigns it.
>
> The tethered ethos guides *do* use `2001:db8::1`, and correctly so: there the
> host sits on the **same link** as the node, with no router in between, so
> on-link resolution is the right behaviour. Do not carry that address over to
> this topology.

---

## Part E — Flash the nodes

Both nodes use the same signing key and the same crypto flags as in tethered
mode. Pass `DEFAULT_CHANNEL` / `DEFAULT_PAN_ID` matching the border router.

### E.1 samr21-xpro

`USE_ETHOS=0` is all it takes — the app Makefile then selects `netdev_default`
(→ `at86rf233` → 6LoWPAN) instead of `stdio_ethos`:

```bash
USE_ETHOS=0 DEFAULT_CHANNEL=26 DEFAULT_PAN_ID=0x23 \
  SUIT_KEY_DIR=$PWD/examples/advanced/suit_update/ed25519-keys SUIT_KEY=ed25519 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

The shell is now on the EDBG UART on its own: `... make term`, no ethos.

### E.2 nRF52840 dongle

Select the radio with `DONGLE_NETIF=radio` (the default is `cdc-ecm`):

```bash
DONGLE_NETIF=radio DEFAULT_CHANNEL=26 DEFAULT_PAN_ID=0x23 \
  SUIT_KEY_DIR=$PWD/examples/advanced/suit_update/ed25519-keys SUIT_KEY=ed25519 \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update clean all -j4
```

This drops `usbus_cdc_ecm` and `gnrc_uhcpc` and adds `netdev_default`
(→ `nrf802154`), while keeping `stdio_cdc_acm`, `usbus_dfu` and the pinned
`ROM_OFFSET`/`ROM_LEN`. Slot geometry is byte-identical to a CDC-ECM build
(`SLOT0_OFFSET 0x4000`, `SLOT1_OFFSET 0x71800`), so nothing about the install
procedure changes.

**The dongle still needs USB.** It has no UART-to-USB bridge, so CDC-ACM is its
only console, and `dfu-util` over USB is its only flashing route (there is no
SWD on this board). Installing is exactly the **two-stage `riotboot_dfu`
procedure** in [DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md) — follow
it verbatim, just with the `DONGLE_NETIF=radio` flag added to every build
command. Only the *update traffic* moves to the radio.

### E.3 Open each node's console and read its address

Unlike the tethered guides, both nodes now get a **plain serial console** — no
ethos, no CDC-ECM script. Run these on whichever machine the board's USB is
plugged into (the Pi or your dev host; the console is independent of the mesh).

**samr21-xpro** — console on the EDBG UART:

```bash
USE_ETHOS=0 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update term PORT=/dev/ttyACM0
```

`USE_ETHOS=0` matters here too: without it `TERMPROG` becomes `ethos` and the
command tries to bring up a network interface instead of opening a terminal.

**nRF52840 dongle** — console on CDC-ACM:

```bash
DONGLE_NETIF=radio BOARD=nrf52840dongle \
  make -C examples/advanced/suit_update term PORT=/dev/ttyACM0
```

> Note this is **different from CDC-ECM mode**, where `make term` runs the
> network script and gives you no console at all (see
> [DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md)). With
> `DONGLE_NETIF=radio` there is no `TERMPROG` override, so `make term` falls
> through to `pyterm` on `$PORT` — a real shell, like any other board.

Both resolve to `pyterm -p $PORT -b 115200`. If you prefer, `picocom -b 115200
/dev/ttyACM0` is equivalent and has no Python dependency.

> **The dongle's console drops whenever the board resets** — on every reboot,
> every DFU flash, and at the end of a successful SUIT update. Its CDC-ACM port
> is provided by the firmware, so it vanishes from USB and re-enumerates, often
> on a *different* `ttyACM*` node. `pyterm` does not reconnect, which looks like
> "the terminal stopped working".
>
> Use the descriptor-based path, which survives renumbering (`ls -l
> /dev/serial/by-id/` to find yours), and a retry loop — the reboot you most
> want to watch is precisely the moment the port disappears:
>
> ```bash
> while true; do
>   picocom -b 115200 /dev/serial/by-id/usb-RIOT-os.org_nrf52840dongle_*-if00 2>/dev/null
>   sleep 1
> done
> ```
>
> The samr21's EDBG port does not have this problem: it is provided by the
> onboard debugger, not by the running firmware, so it stays put across resets.

**Finding the right port** when both boards are attached — they compete for
`/dev/ttyACM0`:

```bash
udevadm info -q property -n /dev/ttyACM0 | grep -E 'ID_MODEL|ID_MODEL_ID'
# samr21-xpro → EDBG_CMSIS-DAP
# nRF52840 dongle → ID_MODEL_ID=7d01
```

Adjust `PORT=` accordingly (commonly the dongle claims `ttyACM0` and the samr21
lands on `ttyACM1`).

Then, in each console:

```
> ifconfig
```

Note the `2001:db8::<eui64>` global address on the 802.15.4 interface — those
are your two `SUIT_CLIENT` values. If a node shows only a `fe80::` link-local
address it has not heard the border router: check channel and PAN ID on both
sides, and confirm the BR itself has a global address (Part C).

---

## Part F — Publish

Publishing is unchanged except that `SUIT_COAP_SERVER` is the Pi, and the
artifacts have to reach the Pi's `coaproot`. **Pin `APP_VER` once** and use it
for both boards.

### F.1 Build on the dev host, ship to the Pi (recommended)

Keys and the ~1.1 GB wolfSSL checkout stay on the dev machine; only artifacts
travel.

```bash
APP_VER=$(date +%s)
KEYS=$PWD/examples/advanced/suit_update/ed25519-keys

USE_ETHOS=0 SUIT_KEY_DIR=$KEYS SUIT_KEY=ed25519 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[fdea:dbee:f::1] \
  make -C examples/advanced/suit_update suit/publish

DONGLE_NETIF=radio SUIT_KEY_DIR=$KEYS SUIT_KEY=ed25519 \
  BOARD=nrf52840dongle APP_VER=$APP_VER SUIT_COAP_SERVER=[fdea:dbee:f::1] \
  make -C examples/advanced/suit_update suit/publish
```

Then the manual manifest-encryption step
([SETUP_COMMON.md](SETUP_COMMON.md), the `encrypt_manifest.py` step — still not
automated), and only then:

```bash
rsync -a --copy-links coaproot/ suitpi.local:~/coaproot/
```

`--copy-links` matters: `suit/publish` creates `riot.suit.latest.bin` as a
symlink.

Artifacts land per board — `coaproot/fw/suit_update/samr21-xpro/` and
`.../nrf52840dongle/` — so one `coaproot` serves both nodes with no collision.

### F.2 Build on the Pi

Identical commands, no `rsync`. Needs the toolchain, the keys, and (for the PQ
variants) the wolfSSL checkout on the Pi. Workable, but the PQ builds `cp -a`
~1.1 GB per clean build — put the checkout on an SSD, not the SD card.

---

## Part G — Notify, from the Pi

The Pi is the only host with a route into the mesh, so triggering happens there:

```bash
SUIT_COAP_SERVER=[fdea:dbee:f::1] SUIT_CLIENT=[2001:db8::<samr21-eui64>] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify

SUIT_COAP_SERVER=[fdea:dbee:f::1] SUIT_CLIENT=[2001:db8::<dongle-eui64>] \
  BOARD=nrf52840dongle make -C examples/advanced/suit_update suit/notify
```

For an encrypted manifest add `SUIT_NOTIFY_MANIFEST=riot.suit.enc`, as in the
tethered guides.

`suit/notify` is only a CoAP POST, so if you used F.1 and have no build tree on
the Pi, this one-liner is equivalent and needs nothing but `aiocoap`:

```bash
aiocoap-client -m POST "coap://[2001:db8::<eui64>]/suit/trigger" \
  --payload "coap://[fdea:dbee:f::1]/fw/suit_update/<board>/riot.suit.enc"
```

Or trigger from the node's own console:

```
> suit fetch coap://[fdea:dbee:f::1]/fw/suit_update/samr21-xpro/riot.suit.enc
```

**Watch the node's console, not the host command** — `suit/notify` routinely
errors or hangs on the host even when the update succeeded.

---

## Bring-up checklist

Run these in order; each one isolates a different layer.

1. `make ... flash` on the border router completes (Part C).
2. `ifconfig` on the BR shows a SLIP interface **and** an 802.15.4 interface
   with a `2001:db8::` address, on the expected channel/PAN.
3. `ping -c3 2001:db8::<eui64>` from the Pi, for **each** node.
4. `aiocoap-client -m GET
   coap://[fdea:dbee:f::1]/fw/suit_update/samr21-xpro/riot.suit.latest.bin` from the
   Pi returns data (and the same for `nrf52840dongle`).
5. A full default-crypto update on each node: `suit_worker: downloading …`
   through to the reboot, then confirm the running slot flipped.

---

## Known rough edges in this topology

- **A node with only a `fe80::` address** heard no router advertisement —
  channel/PAN mismatch, or the BR never came up. Check the BR's `ifconfig`
  before suspecting the node.
- **The manifest URI is frozen at publish time.** `SUIT_COAP_SERVER` must be an
  address the *node* can route to; `[fdea:dbee:f::1]` on the Pi's `sl0` is that
  address. The Pi's LAN address is not, unless you also arrange forwarding and a
  default route.
- **Mini-UART not disabled** (Part A.3) shows up as intermittent,
  irreproducible SLIP breakage — not as a clean failure.
- **samr21 RAM.** Swapping `stdio_ethos` for `netdev_default` + 6LoWPAN changes
  the 32 KB budget, so the marginal PQ combinations in
  [DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md)'s matrix are **not
  automatically still valid here** — they were measured in ethos mode. Re-check
  before relying on one, and watch for the mute-shell symptom (board boots,
  prompt echoes, every `printf` empty) described in
  [GOTCHAS.md](GOTCHAS.md#ram-limits). Ed25519 + X25519 (the default) verified
  building in radio mode. The dongle's 256 KB is not at risk.
