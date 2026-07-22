# Shared setup — everything that is the same on every board

This is the **device-independent half** of the SUIT update workflow. Do this
once, then continue in your board's guide:

- [DEVICE_NATIVE.md](DEVICE_NATIVE.md) — no hardware needed
- [DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md) — SAMR21-xpro over ethos
- [DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md) — nRF52840 Dongle over USB

New to SUIT? Read [GUIDE.md](GUIDE.md) first — it explains what a manifest,
a slot, and the three crypto axes actually are. This file assumes you know
the vocabulary and just want the commands.

Everything below runs in the **WSL2 (Ubuntu) shell** from the repo root
(`~/masterthesis/RIOT` or wherever this checkout lives). Only `usbipd`
commands run in Windows PowerShell, and only for real hardware.

---

## 1. Host prerequisites (once per machine)

### 1.1 Python tooling

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
```

`aiocoap-fileserver` installs to `~/.local/bin`. Make sure it is on your
PATH, or every publish step will look like it silently did nothing:

```bash
echo 'export PATH=$PATH:~/.local/bin' >> ~/.profile && . ~/.profile
which aiocoap-fileserver     # must print a path
```

### 1.2 Compiler + USB tools

```bash
sudo apt-get update
sudo apt-get install -y gcc-arm-none-eabi usbip hwdata usbutils
```

`gcc-arm-none-eabi` is only needed for the two real boards; `native` builds
with your host gcc. `usbip`/`hwdata`/`usbutils` are only needed under WSL2
for real hardware.

### 1.3 Host network helper tools

```bash
make -C dist/tools/ethos      # SAMR21-xpro (serial-to-IP bridge)
make -C dist/tools/uhcpd      # both real boards (serves the IPv6 prefix)
```

Build these **before** the first `setup_network.sh`, or it aborts with
`uhcpd: not found`. The dongle does not use ethos, but does use `uhcpd`.
`native` needs neither (it uses `dist/tools/tapsetup`).

### 1.4 Extra prerequisites for the post-quantum variants

Only needed if you will use `SUIT_KEY_ALGO=ml-dsa-*` or
`SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-*`:

| Requirement | Why | Check |
|---|---|---|
| **OpenSSL 3.5+** | `openssl genpkey -algorithm ml-dsa-65` and the `seed-only` output format | `openssl version` |
| **`cryptography` with ML-DSA support** | `suit-tool sign` uses it to produce the signature | `python3 -c "from cryptography.hazmat.primitives.asymmetric import mldsa"` |
| Local wolfSSL checkout | on-device ML-DSA/ML-KEM; RIOT's pinned `pkg/wolfssl` is too old | auto-selected by the build, nothing to do |

The wolfSSL part is handled for you: `makefiles/suit.base.inc.mk` points the
build at the already-`--enable-dilithium` checkout in
`dist/tools/suit/ml-dsa-example/wolfssl` via RIOT's `PKG_SOURCE_LOCAL_*`
mechanism whenever a PQ algorithm is selected. Be aware it `cp -a`s ~1.1 GB
on every clean build — slow but correct (see
[GOTCHAS.md](GOTCHAS.md#build--toolchain)).

---

## 2. The three crypto axes

Everything in this project is one point in a three-dimensional space. Each
axis is an independent Make variable; **defaults in bold**.

| Axis | Variable | Choices |
|---|---|---|
| **Signature** (authenticity) | `SUIT_KEY_ALGO` | **`ed25519`** · `ml-dsa-44` · `ml-dsa-65` · `ml-dsa-87` |
| **Manifest encryption** (metadata confidentiality) | `SUIT_MANIFEST_ENCRYPT` + `SUIT_MANIFEST_ENCRYPT_ALGO` | **`1`**/`0`; algo **`x25519`** · `ml-kem-768` · `ml-kem-1024` |
| **Payload encryption** (firmware confidentiality) | `SUIT_FIRMWARE_ENCRYPT` | **`1`**/`0` — reuses the manifest axis's `_ALGO` and device key |

Notes that trip people up:

- **Both encryption features are ON by default.** A bare `make flash` builds
  an encryption-capable image, and a bare `make suit/publish` produces an
  **encrypted payload**. To reproduce the classic pre-encryption flow you
  must opt out explicitly on *both* commands.
- **Payload encryption implies manifest encryption's module** (they share the
  device key and crypto primitives), even with `SUIT_MANIFEST_ENCRYPT=0`.
- **Manifest encryption is not automated.** `suit/publish` encrypts the
  *payload* for you, but the manifest must be encrypted by hand afterwards
  (step 6 below). Payload encryption *is* automated.

### 2.1 The matching rule — read this before anything fails

> **Build/flash flags decide what the device *can accept*.
> Publish flags decide what actually *gets produced*.
> Plaintext always passes through.**

So the only forbidden direction is publishing *more* encryption than the
flashed firmware supports:

| Mismatch | Symptom on the device |
|---|---|
| encrypted payload → firmware built `SUIT_FIRMWARE_ENCRYPT=0` | fetch aborts, `Image beyond size` |
| encrypted manifest → firmware built `SUIT_MANIFEST_ENCRYPT=0` | `suit_worker: suit_parse() failed` |
| wrong `_ALGO` (e.g. ML-KEM container, X25519 build) | `recipient alg -25 != built-in -70768` |
| right algo, **wrong key directory** | `manifest decryption failed: -213` (no earlier error — see [GOTCHAS.md](GOTCHAS.md#encryption--keys)) |

**The habit that avoids all of it: use the identical flag set on the flash
command and the publish command.** Every recipe in the device guides is
written that way.

---

## 3. Signing keys

The **public** half of your signing key is baked into the firmware at flash
time. The device accepts only manifests signed by that exact key, so
**flash and publish must use the same `SUIT_KEY*` variables**. Change the
key or the algorithm ⇒ reflash.

Keys live in `$SUIT_KEY_DIR` (default `~/.local/share/RIOT/keys`). Use a
**dedicated directory per algorithm** — do not overwrite `default.pem`, and
do not share one directory between algorithms (the device encryption keys
in §4 are per-directory too).

### 3.1 Ed25519 (classical, the default)

```bash
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/ed25519-keys
dist/tools/suit/gen_key.py examples/advanced/suit_update/ed25519-keys/ed25519.pem

export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/ed25519-keys
export SUIT_KEY=ed25519
# SUIT_KEY_ALGO defaults to ed25519 — no need to set it
```

Skip the generation if the file already exists; reusing it means you do not
have to reflash to pick up a new public key.

### 3.2 ML-DSA (post-quantum, FIPS 204)

Use the `suit/genkey` target — it applies the OpenSSL `seed-only` workaround
automatically. Substitute `44`/`65`/`87` throughout:

```bash
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/mldsa-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=<your-board> \
  make -C examples/advanced/suit_update suit/genkey

export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys
export SUIT_KEY=mldsa65
export SUIT_KEY_ALGO=ml-dsa-65
```

`echo 0 |` answers the interactive "encrypt the private key file?" prompt
with "no". Requires the §1.4 prerequisites.

> **Exports are per-terminal.** This walkthrough uses several terminals. Every
> command in the device guides repeats the variables inline for that reason.
> If you drop them, make silently falls back to
> `~/.local/share/RIOT/keys/default.pem` — the wrong key — and you get the
> misleading `suit_tool.sign - Non-library key type not implemented`.

### 3.3 Per-level sizes (why the buffers differ)

| | Ed25519 | ML-DSA-44 | ML-DSA-65 | ML-DSA-87 |
|---|---|---|---|---|
| Security category | — (classical) | 2 | 3 | 5 |
| COSE algorithm ID | −8 | −48 | −49 | −50 |
| Public key | 32 B | 1312 B | 1952 B | 2592 B |
| Signature | 64 B | 2420 B | 3309 B | 4627 B |
| `SUIT_MANIFEST_BUFSIZE` (auto) | 640 | 3072 | 3840 | 5376 |

The app `Makefile` sets the buffer size for you from `SUIT_KEY_ALGO`; you
never pass it by hand.

---

## 4. Device encryption keys

Separate from the signing key. With encryption on (the default), the **first
build** auto-generates a device keypair *inside `$SUIT_KEY_DIR`* and embeds
its private half in the firmware:

| `SUIT_MANIFEST_ENCRYPT_ALGO` | Generated file | Overhead per manifest |
|---|---|---|
| `x25519` (default) | `$SUIT_KEY_DIR/device_x25519.pem` | ~92 B |
| `ml-kem-768` | `$SUIT_KEY_DIR/device_mlkem768.pem` | ~1144 B |
| `ml-kem-1024` | `$SUIT_KEY_DIR/device_mlkem1024.pem` | ~1696 B |

You can pre-generate without building via the `suit/genenckey` target with
the same variables.

> **These keys are per-directory, not global.** `ed25519-keys/device_mlkem768.pem`
> and `mldsa-keys/device_mlkem768.pem` are *different keypairs*. Encrypting
> against the wrong one fails late and silently — see
> [GOTCHAS.md](GOTCHAS.md#encryption--keys).

---

## 5. The update cycle

Once your board is flashed and networked (device guide), every update is the
same four steps.

### 5.1 Start the CoAP file server (own shell, keep running)

```bash
cd ~/masterthesis/RIOT
mkdir -p coaproot
aiocoap-fileserver coaproot
```

### 5.2 Publish

```bash
APP_VER=$(date +%s)          # pin it — see the warning below
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY \
  BOARD=<board> APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

This builds a fresh firmware image, signs the manifest, encrypts the payload
(if `SUIT_FIRMWARE_ENCRYPT=1`), and copies everything into
`coaproot/fw/suit_update/<board>/`. Look for `published … as coap://…` lines.

> **Always pin `APP_VER`.** It defaults to `$(date +%s)`, evaluated
> *independently* by the parent make and the riotboot sub-make. A few seconds
> of drift and the manifest generator looks for a slot binary that was never
> built: `FileNotFoundError: … slot0.<epoch>.bin`.

> **`APP_VER` is also the manifest sequence number**, and it is a strict
> monotonic anti-rollback counter. Every publish needs a **fresh, larger**
> value or the device correctly rejects it with `res=-5`.

### 5.3 Encrypt the manifest (only if you want manifest confidentiality)

Not automated. Encrypt in place, in the published directory, **for the same
`$SUIT_KEY_DIR` you flashed with**:

```bash
# X25519 (default)
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/<board>/riot.suit.enc \
  coaproot/fw/suit_update/<board>/riot.suit.latest.bin

# ML-KEM variant
python3 examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_mlkem768.pem \
  -o coaproot/fw/suit_update/<board>/riot.suit.enc \
  coaproot/fw/suit_update/<board>/riot.suit.latest.bin
```

Expect `Self-test decrypt: OK` and an overhead report. Keep the output name
exactly **`riot.suit.enc`** — short names keep the URL well inside the
device's path buffer. (The tool also drops `encrypted.h` / `plaintext.h` /
`device_*.h` helper headers in the current directory; they are
standalone-example artifacts, safe to delete.)

### 5.4 Notify

```bash
# plaintext manifest (no step 5.3)
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=<device-address> \
  BOARD=<board> make -C examples/advanced/suit_update suit/notify

# encrypted manifest (after 5.3)
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=<device-address> \
  BOARD=<board> make -C examples/advanced/suit_update suit/notify
```

Or trigger straight from the RIOT shell:

```
> suit fetch coap://[2001:db8::1]/fw/suit_update/<board>/riot.suit.enc
```

`SUIT_CLIENT` is board-specific (`[fe80::2%riot0]` for ethos,
`[fe80::2%$ECM_IFACE]` for the dongle) — see your device guide.

> **Judge success on the device terminal, never the host command's exit
> code.** `suit/notify` frequently prints a network error or hangs and needs
> Ctrl-C even when the trigger arrived and the update succeeded.

### 5.5 What a successful update looks like

```
suit_worker: downloading "coap://[2001:db8::1]/fw/suit_update/<board>/riot.suit.enc"
suit_worker: got manifest with size 577
suit_worker: manifest decrypted (485 bytes)      # or: manifest not encrypted, passing through
suit: verifying manifest signature
suit: validated manifest version
Manifest seq_no: <new>, highest available: <old>
... vendor ID: OK ... class id: OK ... SUIT policy check OK.
riotboot_flashwrite: initializing update to target slot 1
suit: decrypting payload (header 74 bytes)       # payload encryption on
Fetching firmware |█████████████████████████| 100%
suit: payload decrypted (108028 bytes)
Verified installed payload
suit_worker: update successful
suit_worker: rebooting...
...
Running from slot 1
```

The last line is the proof: the device booted the *other* riotboot slot.

---

## 6. Reading the error codes

| Code | Meaning | Usually |
|---|---|---|
| `res=-4` | manifest condition failed | class-ID or slot-offset mismatch |
| `res=-5` | `seq_nr <= running image` | **expected** on replay — publish a newer `APP_VER` |
| `res=-7` | payload digest mismatch | bad storage write path, not crypto |
| `res=-50` | `SUIT_ERR_STORAGE` | no valid riotboot slot (wrong flashing method) |
| `res=-125` | wolfCrypt `MEMORY_E` | ML-KEM heap exhaustion — see [FINDINGS.md](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery) |
| `res=-213` | AEAD tag mismatch | wrong device key, or tampered container |

Full explanations: [GOTCHAS.md](GOTCHAS.md).

---

## 7. Where to go next

| You want | Go to |
|---|---|
| The concepts behind all this | [GUIDE.md](GUIDE.md) |
| Your board's flash + network steps and its combination matrix | the three `DEVICE_*.md` guides |
| Why something failed | [GOTCHAS.md](GOTCHAS.md) |
| Whether a combination fits, and the measurements behind it | [FINDINGS.md](FINDINGS.md) |
| The COSE wire formats | [manifest-encryption/README.md](manifest-encryption/README.md), [manifest-encryption-mlkem/README.md](manifest-encryption-mlkem/README.md), [firmware-encryption/README.md](firmware-encryption/README.md) |
