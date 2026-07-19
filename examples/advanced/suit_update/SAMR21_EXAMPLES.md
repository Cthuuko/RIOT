# SUIT update examples: SAMR21-xpro, Ed25519 vs. ML-DSA

Four copy-pasteable walkthroughs for the same board, differing only in the
signing algorithm baked into the firmware and used to sign updates:

- **Example A — Ed25519** (classical, RIOT's default)
- **Example B — ML-DSA-65** (post-quantum, FIPS 204, category 3)
- **Example C — ML-DSA-44** (post-quantum, FIPS 204, category 2)
- **Example D — ML-DSA-87** (post-quantum, FIPS 204, category 5)

plus one orthogonal add-on that layers on top of any of them:

- **Example E — Encrypted manifests** (X25519 + ChaCha20-Poly1305
  confidentiality; **unverified draft**, see its own status note)

Run each command as its own line in the WSL shell, from the repo root
(`~/masterthesis/RIOT` or wherever this checkout lives). All four examples
share the same one-time setup (Parts 0–2); only key generation, flashing,
and publishing (Parts 3–5) differ, driven by the `SUIT_KEY*` variables.

Don't run two examples' `term` at the same time — they all want
`/dev/ttyACM1`. Switching from one example to another just means: flash
the other firmware, then publish+notify with the matching key.

**Hardware status**: A, B, and C are verified working end-to-end on real
samr21-xpro (see [MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md) for A/B;
C confirmed the same way — full `suit_worker: update successful` and boot
from the new slot). D (ML-DSA-87) generalizes the same code path (see
[MLDSA_MULTILEVEL_CHANGES.md](MLDSA_MULTILEVEL_CHANGES.md)) but as of this
writing has only been exercised on `BOARD=native64` — **not** on real
hardware. ML-DSA-87's ~2.6KB public key / ~4.6KB signature are notably
bigger than ML-DSA-65's and are expected **not** to fit — B's RAM budget on
this board already left only ~216B spare with the smaller ML-DSA-65 key.
Treat Example D as a "does it even build and flash" experiment, not a
proven flow.

**Since manifest encryption landed (default-on, see Example E)**: builds of
A/B/C now also embed an auto-generated X25519 device key, and their
unencrypted publishes keep working via the device-side pass-through
(`suit_worker: manifest not encrypted, passing through` in the terminal —
that line is the only visible change). Build with `SUIT_MANIFEST_ENCRYPT=0`
to reproduce the exact pre-encryption images.

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

Leave both running for the rest of the walkthrough (all four examples
reuse them).

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

# Example C — ML-DSA-44 (post-quantum signatures, category 2)

Same shape as Example B; the differences are `SUIT_KEY_ALGO=ml-dsa-44`
plumbed through every command, a different key directory, and a smaller
signature (2420 bytes vs. ML-DSA-65's 3309). **Verified working end-to-end
on real samr21-xpro hardware** — full `suit_worker: update successful`,
reboot, and boot from the new slot, confirming `MLDSA_MULTILEVEL_CHANGES.md`'s
expectation that ML-DSA-44 fits this board's 32KB RAM with headroom to
spare.

## C.3 — Generate a signing key

```sh
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/mldsa44-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-44 \
  SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa44-keys \
  SUIT_KEY=mldsa44 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update suit/genkey
```

Same OpenSSL 3.5+ / `cryptography` ML-DSA requirements as B.3.

## C.4 — Export the key selection for every command below

```sh
export SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa44-keys
export SUIT_KEY=mldsa44
export SUIT_KEY_ALGO=ml-dsa-44
```

Same caveat as A.4/B.4 — exports are per-terminal; C.5 and C.7 repeat the
variables inline.

## C.5 — Flash the board

Close any running `make term` / ethos terminal first (same caveat as A.5).

```sh
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa44-keys \
  SUIT_KEY=mldsa44 SUIT_KEY_ALGO=ml-dsa-44 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

This build embeds wolfCrypt's ML-DSA-44 verifier, `SUIT_MANIFEST_BUFSIZE=3072`
(smaller than B's 3840 — ML-DSA-44's signature is smaller), and the same
`SUIT_WORKER_STACKSIZE=4096` used for every ML-DSA level — see
[MLDSA_MULTILEVEL_CHANGES.md](MLDSA_MULTILEVEL_CHANGES.md) for the
per-level constants table. This links and flashes cleanly with headroom to
spare, unlike Example D's ML-DSA-87.

## C.6 — Open the board's terminal

```sh
BOARD=samr21-xpro PORT=/dev/ttyACM1 make -C examples/advanced/suit_update term
```

```sh
ping -c3 fe80::2%riot0
```

## C.7 — Publish a signed update

```sh
APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa44-keys \
  SUIT_KEY=mldsa44 SUIT_KEY_ALGO=ml-dsa-44 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

## C.8 — Notify the device to fetch and apply it

```sh
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Watch the board's terminal: manifest fetch, `suit: verifying manifest
signature`, firmware download, reboot — same shape as B.8, expected to be
somewhat faster given ML-DSA-44's smaller lattice dimension.

---

# Example D — ML-DSA-87 (post-quantum signatures, category 5)

Same shape as Example B; the differences are `SUIT_KEY_ALGO=ml-dsa-87`
plumbed through every command, a different key directory, and a
considerably bigger signature (4627 bytes vs. ML-DSA-65's 3309). **Confirmed
not to fit on real samr21-xpro hardware**: D.5 (flash) fails at link time —
`.bss` overflows the 32KB `ram` region by 3628 bytes:

```
arm-none-eabi/bin/ld: .../slot0.elf section `.bss' will not fit in region `ram'
arm-none-eabi/bin/ld: region `ram' overflowed by 3628 bytes
collect2: error: ld returned 1 exit status
```

This matches `MLDSA_MULTILEVEL_CHANGES.md`'s expectation that ML-DSA-87's
larger public key (2592B) and signature material don't fit this board's
32KB RAM — B's ML-DSA-65 build already only had ~216B of headroom to spare
(see [MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md)). Example D is kept
here as a documented dead end, not a working walkthrough: D.3–D.4 (key
generation) work fine, but D.5 onward cannot succeed without shrinking
`.bss` by at least ~3.6KB (the same kind of stack/heap surgery documented
in `MLDSA_HARDWARE_FIXES.md` for ML-DSA-65, redone against the bigger
category-5 sizes) — treat D.6–D.8 as unreachable until that's done.

## D.3 — Generate a signing key

```sh
cd ~/masterthesis/RIOT
mkdir -p examples/advanced/suit_update/mldsa87-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-87 \
  SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa87-keys \
  SUIT_KEY=mldsa87 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update suit/genkey
```

Same OpenSSL 3.5+ / `cryptography` ML-DSA requirements as B.3.

## D.4 — Export the key selection for every command below

```sh
export SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa87-keys
export SUIT_KEY=mldsa87
export SUIT_KEY_ALGO=ml-dsa-87
```

Same caveat as A.4/B.4 — exports are per-terminal; D.5 and D.7 repeat the
variables inline.

## D.5 — Flash the board

Close any running `make term` / ethos terminal first (same caveat as A.5).

```sh
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa87-keys \
  SUIT_KEY=mldsa87 SUIT_KEY_ALGO=ml-dsa-87 \
  BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4
```

This build embeds wolfCrypt's ML-DSA-87 verifier and
`SUIT_MANIFEST_BUFSIZE=5376` (bigger than B's 3840). **This fails to link**,
as shown above — `region ram overflowed by 3628 bytes` — before it ever
reaches the board. (That 3,628 figure predates default-on manifest
encryption; with today's default `SUIT_MANIFEST_ENCRYPT=1` the overflow is
3,756 B — the extra 128 B buffer pad — and `SUIT_MANIFEST_ENCRYPT=0`
reproduces the original number. Either way: no fit.) This isn't a sign something else is broken; it's
`MLDSA_MULTILEVEL_CHANGES.md`'s predicted RAM shortfall confirmed exactly.
Shrinking `.bss` by ~3.6KB would need the same kind of stack/heap surgery
documented in [MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md) for
ML-DSA-65, redone against the bigger category-5 key/signature sizes.

## D.6 — Open the board's terminal (unreachable — D.5 doesn't link)

```sh
BOARD=samr21-xpro PORT=/dev/ttyACM1 make -C examples/advanced/suit_update term
```

```sh
ping -c3 fe80::2%riot0
```

## D.7 — Publish a signed update (unreachable — D.5 doesn't link)

```sh
APP_VER=$(date +%s)
SUIT_KEY_DIR=~/masterthesis/RIOT/examples/advanced/suit_update/mldsa87-keys \
  SUIT_KEY=mldsa87 SUIT_KEY_ALGO=ml-dsa-87 \
  BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

## D.8 — Notify the device to fetch and apply it (unreachable — D.5 doesn't link)

```sh
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

D.6–D.8 are included only to keep the walkthrough's shape consistent with
A/B/C — none of them are reachable until the `.bss` overflow in D.5 is
fixed. If that gets fixed later, this step would watch the board's
terminal for manifest fetch, signature verification, and reboot; even past
the link error, the bigger manifest buffer and in-flight signature leave
less remaining RAM headroom than B/C, so a crash or hang here wouldn't be
surprising either.

---

# Example E — Encrypted manifests (X25519 + ChaCha20-Poly1305)

> **⚠ UNVERIFIED DRAFT — to be verified on real hardware.** This walkthrough
> is written from the working `native64` end-to-end flow
> (`MANIFEST_ENCRYPTION_CHANGES.md`) and has **not** yet been executed on a
> real samr21-xpro (plan Step 5b in `MANIFEST_ENCRYPTION_PLAN.md`). Commands
> and expected log lines follow the same conventions as A–D but may need
> correction during bring-up; update this section (and drop this banner)
> once a full encrypted update has succeeded on the board.

Encryption is orthogonal to the signing algorithm: it wraps the *signed*
manifest in a COSE_Encrypt container (ephemeral-static X25519 → HKDF-SHA256
→ ChaCha20-Poly1305, ~92B overhead) that the device decrypts in place
before parsing. It is **on by default** in this app's build
(`SUIT_MANIFEST_ENCRYPT=1`), so A/B/C flashes are already
encryption-capable — Example E only changes what gets *published*.

**Measured RAM feasibility** (link-level, from the 12-combo matrix in
`MLKEM_ENCRYPTION_CHANGES.md` / `mlkem-feasibility-matrix-raw.txt` — all
X25519-encryption builds link except on top of D; RAM out of 32,768 B):

| Base example | Measured on samr21-xpro (X25519 encryption) |
|---|---|
| E on top of A (Ed25519) | ✅ fits, 21,160 B RAM (**11.6 KB spare**) — recommended first target |
| E on top of C (ML-DSA-44) | ✅ fits, 30,888 B RAM (1.9 KB spare) |
| E on top of B (ML-DSA-65) | ✅ **links**, 32,680 B RAM (**88 B spare** — tighter than B's ~216 B baseline; expect zero tolerance for any growth, treat runtime stability as unproven until tested) |
| E on top of D (ML-DSA-87) | ❌ `.bss` overflow 3,756 B (D already fails plain) |

**Post-quantum encryption variant**: `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768`
(or `ml-kem-1024`) replaces the X25519 recipient with an ML-KEM
encapsulation — including the **full-PQ combo C + ML-KEM-768, which fits
with 1,160 B spare** thanks to the `suit_pq_scratch` union (the host-side
encrypt step then uses `manifest-encryption-mlkem/encrypt_manifest.py
--key $SUIT_KEY_DIR/device_mlkem768.pem`). Same walkthrough shape;
details, measured sizes, and gotchas in `MLKEM_ENCRYPTION_CHANGES.md` —
equally **unverified on hardware** until this section's E.9 checklist has
been run for it.

## E.0 — Prerequisite

Complete a working base example first (A recommended) up to and including
its flash step — the flash **must** be from a build with encryption on
(the default), which also auto-generates the device key
`$SUIT_KEY_DIR/device_x25519.pem` and bakes its private half into the
firmware. Keep the A.4-style exports set; E reuses them. (To pre-generate
the key without building: `suit/genenckey` with the same variables.)

## E.5 — Flash (only if the board isn't already running an encryption-capable build)

Identical to A.5/B.5/C.5 — no extra variables needed; `make` prints
`suit: generating manifest-encryption key in ...` on the first build.
Reflashing after the device key already exists reuses it.

## E.7 — Publish a signed update, then encrypt the manifest

Publish exactly as in A.7/B.7/C.7 (same variables, fresh
`APP_VER=$(date +%s)`), then encrypt the published manifest **for this
board's device key**. Publishing does not drop files at the `coaproot/`
root — `suit/publish` copies into
`$(SUIT_COAP_FSROOT)/$(SUIT_COAP_BASEPATH)` =
`coaproot/fw/suit_update/samr21-xpro/` (as the `published ... as coap://...`
lines in its output show), so encrypt in place there:

```sh
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/samr21-xpro/riot.suit.enc \
  coaproot/fw/suit_update/samr21-xpro/riot.suit.latest.bin
```

Keep the output name exactly `riot.suit.enc`: the notify URL becomes
`coap://[2001:db8::1]/fw/suit_update/samr21-xpro/riot.suit.enc` — 61 of
the device's 64 URL-buffer chars. A versioned name
(`riot.suit.<epoch>.enc`) would silently truncate.

Expect `Self-test decrypt: OK` and a ~92-byte overhead report. (The tool
also drops `encrypted.h`/`plaintext.h`/`device_*.h` helper headers in the
current directory — standalone-example artifacts, safe to delete.)

## E.8 — Notify the device with the encrypted manifest

Point the notify at the encrypted file instead of the default name:

```sh
SUIT_NOTIFY_MANIFEST=riot.suit.enc \
  SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Alternatively, trigger directly from the board's shell (E.6 terminal),
using the full published path:

```
> suit fetch coap://[2001:db8::1]/fw/suit_update/samr21-xpro/riot.suit.enc
```

(61 chars — right under the worker's 64-char URL buffer, which truncates
silently; that's also why E.7 insists on the short `riot.suit.enc` name.)

## E.9 — What "verified" looks like

Watch the board's terminal; the expected sequence (observed on native64):

```
suit_worker: got manifest with size <N+92>
suit_worker: manifest decrypted (<N> bytes)
suit: verifying manifest signature
...
suit_worker: update successful
```

followed by the reboot into the new slot. Bring-up checklist to promote
this section to "verified":

1. Encrypted update end-to-end (decrypt → verify → download → reboot).
2. Plaintext publish still accepted (pass-through line, A.7/A.8 unchanged).
3. Tampered container rejected: flip one byte in `riot.suit.enc`, re-notify
   with it, expect `suit_worker: manifest decryption failed. res=-213`
   (bump nothing — a rejected manifest doesn't consume the seqnr).
4. Repeat update cycle twice more (RAM stability, as in the ML-DSA
   bring-up), then record the `text/data/bss` numbers here and in
   `MANIFEST_ENCRYPTION_CHANGES.md`.

Failure modes to expect on this board (from the feasibility notes): a
`.bss`/`ram` overflow at link time on top of ML-DSA-65 (like Example D's
error, smaller), or a worker-stack shortfall during decrypt — re-measure
with `ps` stack watermarks, and remember the M0+ lesson from
`MLDSA_HARDWARE_FIXES.md`: big structs go `static`, never on the 4KB worker
stack.

---

## Gotchas that apply to all examples

- **`res=-5` / `seq_nr <= running image` on re-notify is expected** — the
  manifest sequence number must strictly increase. Publish with a fresh
  `APP_VER=$(date +%s)` to update again.
- **Flashing while `make term`/ethos is attached floods the terminal with
  `$` garbage** (the EDBG UART emits raw programming noise). Always close
  the terminal before A.5/B.5/C.5/D.5, reopen after.
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
  terminal without the A.4/B.4/C.4/D.4 exports), so signing fell back to
  `~/.local/share/RIOT/keys/default.pem` — which Python's `cryptography`
  may be unable to parse (for instance an ML-DSA key in OpenSSL's combined
  seed+expanded format instead of seed-only). Re-run the command with the
  key variables set, as written in A.7/B.7/C.7/D.7.
