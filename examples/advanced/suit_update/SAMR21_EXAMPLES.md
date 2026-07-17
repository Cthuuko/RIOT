# SUIT update examples: SAMR21-xpro, Ed25519 vs. ML-DSA-65

Two copy-pasteable walkthroughs for the same board, differing only in the
signing algorithm baked into the firmware and used to sign updates:

- **Example A — Ed25519** (classical, RIOT's default)
- **Example B — ML-DSA-65** (post-quantum, FIPS 204)

Run each command as its own line in the WSL shell, from the repo root
(`~/masterthesis/RIOT` or wherever this checkout lives). Both examples
share the same one-time setup (Parts 0–2); only key generation, flashing,
and publishing (Parts 3–5) differ, driven by the `SUIT_KEY*` variables.

Don't run both examples' `term` at the same time — they both want
`/dev/ttyACM1`. Switching from one example to the other just means:
flash the other firmware, then publish+notify with the other key.

---

## Part 0 — One-time host setup

Only needed once per machine.

```sh
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
sudo apt-get install -y gcc-arm-none-eabi usbip hwdata usbutils
```

Attach the board's EDBG USB port to WSL (Windows side, admin PowerShell,
one-time per boot/replug — see
[HARDWARE_SAMR21_WSL.md](HARDWARE_SAMR21_WSL.md) Part A for the full
`usbipd` walkthrough if you haven't done this before):

```powershell
usbipd attach --wsl --busid <busid-from-usbipd-list>
```

Confirm the board shows up as `/dev/ttyACM1` (check `lsusb` /
`udevadm info -q property -n /dev/ttyACM1 | grep ID_MODEL` — it should say
`EDBG_CMSIS-DAP`; if you also have an nRF52840 dongle plugged in, that one
usually claims `/dev/ttyACM0` instead).

## Part 1 — Build the host networking tools (one-time)

```sh
cd ~/masterthesis/RIOT
make -C dist/tools/ethos
make -C dist/tools/uhcpd
```

## Part 2 — Start the network bridge (once per session, keep running)

In a **dedicated terminal**, keep this running for the whole session:

```sh
sudo dist/tools/ethos/setup_network.sh riot0 2001:db8::/64
```

In a **second terminal**:

```sh
sudo ip address add 2001:db8::1/128 dev riot0
mkdir -p ~/masterthesis/RIOT/coaproot
cd ~/masterthesis/RIOT && aiocoap-fileserver coaproot
```

Leave both running for the rest of the walkthrough (both examples reuse
them).

---

# Example A — Ed25519 (classical signatures)

## A.3 — Generate a signing key

```sh
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/ed25519-keys
dist/tools/suit/gen_key.py examples/advanced/suit_update/ed25519-keys/ed25519.pem
```

Generates a fresh Ed25519 keypair. Skip this step if the file already
exists — reusing it means you don't need to reflash to pick up a new
public key.

## A.4 — Export the key selection for every command below

```sh
export SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/ed25519-keys
export SUIT_KEY=ed25519
```

(`SUIT_KEY_ALGO` defaults to `ed25519` — no need to set it.)

**These exports only exist in the terminal you ran them in.** The
walkthrough uses several terminals; the commands in A.5 and A.7 repeat the
variables inline so they are safe to paste into any terminal. If you drop
them, make silently falls back to `~/.local/share/RIOT/keys/default.pem`
(wrong key — see the gotchas at the bottom).

## A.5 — Flash the board

Close any running `make term` / ethos terminal first — flashing and ethos
both need exclusive access to `/dev/ttyACM1`.

```sh
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/ed25519-keys \
  SUIT_KEY=ed25519 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

The key variables matter here too: this step bakes the key's **public**
half into the firmware, and the device only accepts manifests signed with
that same key.

## A.6 — Open the board's terminal (also carries the network link)

```sh
BOARD=samr21-xpro PORT=/dev/ttyACM1 make -C examples/advanced/suit_update term
```

Confirm reachability from a **third terminal**:

```sh
ping -c3 fe80::2%riot0
```

## A.7 — Publish a signed update

```sh
APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/ed25519-keys \
  SUIT_KEY=ed25519 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

Builds a new firmware image, signs the manifest with the Ed25519 key, and
copies both into `coaproot/`.

## A.8 — Notify the device to fetch and apply it

```sh
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Watch the board's terminal (A.6): manifest fetch, signature verification
(fast — well under a second for Ed25519), firmware download progress bar,
reboot into the other slot. To publish another update, repeat A.7–A.8 with
a fresh `APP_VER`.

---

# Example B — ML-DSA-65 (post-quantum signatures)

Same shape as Example A; the differences are `SUIT_KEY_ALGO=ml-dsa-65`
plumbed through every command, a different key directory, and a visibly
slower (but still sub-second on this board) signature verification step.

## B.3 — Generate a signing key

```sh
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/mldsa-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update suit/genkey
```

`suit/genkey` interactively asks whether to encrypt the private key file;
the `echo 0 |` answers "no encryption" non-interactively. Needs OpenSSL
3.5+ and a `cryptography` build with ML-DSA support (see
[dist/tools/suit/ml-dsa-example/README.md](../../../dist/tools/suit/ml-dsa-example/README.md)
if `genpkey -algorithm ml-dsa-65` fails).

## B.4 — Export the key selection for every command below

```sh
export SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys
export SUIT_KEY=mldsa65
export SUIT_KEY_ALGO=ml-dsa-65
```

Same caveat as A.4: exports are per-terminal, so B.5 and B.7 repeat the
variables inline. Forgetting them is worse here — the build would fall
back to the default Ed25519 configuration entirely.

## B.5 — Flash the board

Close any running `make term` / ethos terminal first (same caveat as A.5).

```sh
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

This build embeds wolfCrypt's ML-DSA-65 verifier and needs the RAM/stack/
buffer-size tuning already committed in this app's `Makefile` (only
active under `SUIT_KEY_ALGO=ml-dsa-65`) — see
[MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md) if you're curious why
those exist; nothing extra to do here, they apply automatically.

## B.6 — Open the board's terminal

```sh
BOARD=samr21-xpro PORT=/dev/ttyACM1 make -C examples/advanced/suit_update term
```

```sh
ping -c3 fe80::2%riot0
```

## B.7 — Publish a signed update

```sh
APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 SUIT_KEY_ALGO=ml-dsa-65 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

Same as A.7, but `suit-tool sign` uses the ML-DSA-65 key — the manifest is
several KB bigger (a 3309-byte signature vs. Ed25519's 64 bytes).

## B.8 — Notify the device to fetch and apply it

```sh
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Watch the board's terminal: manifest fetch (bigger, ML-DSA manifest),
`suit: verifying manifest signature` (pauses noticeably longer than
Ed25519 — ML-DSA-65 lattice math on a 48MHz Cortex-M0+), firmware download,
reboot.

---

## Gotchas that apply to both examples

- **`res=-5` / `seq_nr <= running image` on re-notify is expected** — the
  manifest sequence number must strictly increase. Publish with a fresh
  `APP_VER=$(date +%s)` to update again.
- **Flashing while `make term`/ethos is attached floods the terminal with
  `$` garbage** (the EDBG UART emits raw programming noise). Always close
  the terminal before A.5/B.5, reopen after.
- **`suit/notify` on the host often prints a network error or just hangs
  and needs Ctrl-C** even when the trigger reached the device — check the
  board's terminal, not the host command's exit code, to see what actually
  happened.
- **Switching keys without reflashing will fail.** The verifying public
  key is baked into the firmware at flash time; `suit/publish` must use
  the same key the currently-flashed firmware trusts.
- **`suit_tool.sign - Non-library key type not implemented` means the
  wrong key file was picked up, not a missing feature.** It appears when
  `SUIT_KEY_DIR`/`SUIT_KEY` weren't set for the command (e.g. a fresh
  terminal without the A.4/B.4 exports), so signing fell back to
  `~/.local/share/RIOT/keys/default.pem` — which Python's `cryptography`
  may be unable to parse (for instance an ML-DSA key in OpenSSL's combined
  seed+expanded format instead of seed-only). Re-run the command with the
  key variables set, as written in A.7/B.7.
