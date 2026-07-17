# CLAUDE.md — SUIT firmware update example

Place this file at `examples/advanced/suit_update/CLAUDE.md` in your RIOT
checkout. Claude Code loads it automatically whenever you're working inside
this directory, on top of any root-level CLAUDE.md.

## Scope

This example demonstrates SUIT-compliant firmware updates over CoAP. RIOT's
tree is huge (boards, drivers, packages); almost none of it is relevant here.
Stay scoped to the paths below unless a task explicitly requires touching
board/driver code.

## Environment

- Built and run entirely inside WSL2 (Ubuntu), driven from VS Code
  Remote-WSL. All `make`, flashing, and networking commands below run in the
  WSL shell, not PowerShell/CMD.
- `RIOTBASE` resolves to the repo root via `$(CURDIR)/../../..` from this
  directory (`suit_update` → `advanced` → `examples` → repo root).

## Files in this example

| File | Purpose |
|---|---|
| `Makefile` | App config: SUIT modules, ethos vs. border-router networking, storage backend selection, block size |
| `Makefile.board.dep` | Picks `suit_storage_ram`+`suit_storage_vfs` for native, `suit_storage_flashwrite` for real boards |
| `Makefile.suit.custom` | Custom targets: `APP_VER` (epoch-based), native fake-payload generation, `BINDIR_APP` |
| `Makefile.ci` | Boards excluded from CI (insufficient RAM/flash) |
| `main.c` | Shell commands (slot/storage inspection, GPIO-triggered update), riotboot integration |
| `coap_handler.c` | CoAP resources: `/suit/trigger`, board name, version, active/inactive slot |
| `README.md` | Overview + prerequisites |
| `README.native.md` | Full no-hardware walkthrough (native/Linux target) |
| `README.hardware.md` | Full real-hardware walkthrough (ethos, border router, flashing) |
| `native_steps.svg` | Diagram referenced by README.native.md |
| `tests-with-config/` | Automated test configs |

## Supporting RIOT subsystems it depends on

- `sys/suit/` — core manifest parser/handlers/policy (`suit.c`, `handlers*.c`, `policy.c`, `conditions.c`, `storage/`, `transport/`)
- `sys/riotboot/` — bootloader slot management (`slot.c`, `flashwrite.c`, `hdr.c`) used by the real-hardware storage backend
- `dist/tools/suit/` — `gen_key.py`, `gen_manifest.py`, `password_protect_key.py`, `suit-manifest-generator/`
- `dist/tools/ethos/` — Ethernet-over-serial bridge + `setup_network.sh` (wired hardware workflow)
- `dist/tools/uhcpd/` — micro-DHCP daemon paired with ethos
- `dist/tools/tapsetup/` — tap/bridge setup for the native workflow
- `makefiles/suit.inc.mk`, `makefiles/suit.base.inc.mk` — global SUIT Make variables/targets

## Python prerequisites

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
# aiocoap tools install to ~/.local/bin — make sure it's on PATH
```

## Native workflow (no hardware)

```bash
sudo dist/tools/tapsetup/tapsetup -c
sudo ip address add 2001:db8::1/64 dev tapbr0

# separate shell, keep running:
aiocoap-fileserver coaproot

BOARD=native make -C examples/advanced/suit_update all term
# in the RIOT shell:
> ifconfig 6 add 2001:db8::2/64

# generate + sign + publish a payload, then pull it from the device:
echo "AABBCCDD" > coaproot/payload.bin
dist/tools/suit/gen_manifest.py --urlroot coap://[2001:db8::1]/ --seqnr 1 --uuid-class native64 \
  -o suit.tmp coaproot/payload.bin:0:ram:0
dist/tools/suit/suit-manifest-generator/bin/suit-tool create -f suit \
  -i suit.tmp -o coaproot/suit_manifest
dist/tools/suit/suit-manifest-generator/bin/suit-tool sign \
  -k keys/default.pem -m coaproot/suit_manifest -o coaproot/suit_manifest.signed
> suit fetch coap://[2001:db8::1]/suit_manifest.signed
```

## Hardware workflow (example board: samr21-xpro)

```bash
sudo dist/tools/ethos/setup_network.sh riot0 2001:db8::/64

BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4

sudo ip address add 2001:db8::1/128 dev riot0
BOARD=samr21-xpro make -C examples/advanced/suit_update term
```

## Gotchas

- Default `BOARD` is `samr21-xpro`; override with `BOARD=<name>`.
- On a 64-bit host, `BOARD=native` resolves to the actual board `native64`
  (`make` prints `using BOARD="native64" as "native" on a 64-bit system`).
  This changes two things:
  - The tap interface shows up as netif `6`, not `5`, in `ifconfig`.
  - `SUIT_CLASS_ID` embedded in the firmware is `"native64"`. When calling
    `gen_manifest.py` manually (bypassing the `suit/publish` Make target,
    which sets `SUIT_CLASS=$(BOARD)` for you automatically), pass
    `--uuid-class native64` or the device will reject the manifest with
    `suit_worker: suit_parse() failed. res=-4` (class ID mismatch — check
    the printed "Comparing X to Y from manifest" line to confirm).
- If `SUIT_KEY_DIR`'s `default.pem` has been regenerated as a non-ed25519,
  non-ML-DSA key, `suit-tool sign` fails with
  `Non-library key type not implemented`. Generate a dedicated ed25519 key
  instead of overwriting the default: `dist/tools/suit/gen_key.py
  keys/native_ed25519.pem`, then build with `SUIT_KEY_DIR=<dir>
  SUIT_KEY=native_ed25519 make ...` and sign with `-k keys/native_ed25519.pem`
  so the embedded pubkey matches the signing key.
- **ML-DSA (post-quantum) manifest signing is supported**, tooling-only:
  `SUIT_KEY_ALGO=ml-dsa-65 make suit/genkey` (or `suit-tool keygen -t
  ml-dsa-65`) generates an ML-DSA-65 key, `%.pem.pub`/`public_key.h`
  generation and `suit-tool sign` both handle it like Ed25519 (see
  `dist/tools/suit/ml-dsa-example/README.md` for the crypto background).
  Requires OpenSSL 3.5+ and a `cryptography` build with ML-DSA support.
  **On-device verification is not implemented** — `pkg/libcose` (used by
  `sys/suit/handlers_envelope.c`) has no PQC algorithm support, so firmware
  built with an ML-DSA key will still reject the manifest at the
  `_auth_handler`/`cose_sign_verify` step. This is a known, deliberate gap,
  not a bug.
- `USE_ETHOS=1` by default for real hardware (serial-over-IP); set
  `USE_ETHOS=0` and use a border router instead for wireless (BLE/802.15.4) setups.
- Signing keys live in `SUIT_KEY_DIR`, default `~/.local/share/RIOT/keys` —
  auto-generated on first use, or manually via the `suit/genkey` target.
- USB/serial flashing from WSL2 requires `usbipd-win` passthrough from
  Windows. Check `lsusb` / `/dev/ttyACM*` visibility in WSL before assuming a
  flash failure is a code problem.
- Upstream README flags this SUIT implementation as not security-audited —
  don't treat it as production-ready when suggesting deployment changes.
