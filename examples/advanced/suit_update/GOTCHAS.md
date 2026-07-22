# Gotchas — every known pitfall, grouped by symptom

Consolidated from the samr21, dongle, and native bring-ups plus the
implementation notes. If something failed, start with §0 (symptom lookup),
then read the matching section.

Companion docs: [SETUP_COMMON.md](SETUP_COMMON.md) (the correct steps),
[FINDINGS.md](FINDINGS.md) (why the constraints exist).

---

## 0. Symptom → cause lookup

| What you see | Section |
|---|---|
| `suit_parse() failed. res=-5`, `seq_nr <= running image` | [Expected, not a bug](#expected-behaviour-that-looks-like-a-bug) |
| `suit_parse() failed. res=-4`, `offset does not match` | [Flash geometry](#flash-geometry-nrf52840-dongle) |
| `suit_parse() failed. res=-4`, class-ID mismatch | [Class ID](#class-id-native64) |
| `suit_parse() failed. res=-50`, `update failed, hdr invalid` | [No valid slot](#no-valid-riotboot-slot-res-50) |
| `manifest decryption failed: -213` | [Wrong device key](#encryption--keys) |
| `CEK derivation failed: -125` | [ML-KEM memory](#ml-kem-memory-the-big-one) |
| `recipient alg -25 != built-in -70768` | [Algo mismatch](#encryption--keys) |
| `Image beyond size` during fetch | [The matching rule](#encryption--keys) |
| `Erasing bad payload`, `res=-7` | [Digest vs. crypto](#digest-fails-but-the-aead-tag-passed) |
| `suit_tool.sign - Non-library key type not implemented` | [Signing keys](#signing-keys) |
| `FileNotFoundError: … slot0.<epoch>.bin` | [APP_VER drift](#app_ver-epoch-drift) |
| `dfu-util: Could not open file … slot0.<epoch>.bin` | [APP_VER drift](#app_ver-epoch-drift) |
| `Cannot open DFU device 1209:7d02` | [dfu-util permissions](#usb--wsl2) |
| `region 'ram' overflowed by N bytes` | [RAM limits](#ram-limits) |
| `cose/sign.h: No such file` | [Make overrides](#build--toolchain) |
| Terminal floods with `$` garbage | [Serial contention](#serial-port-contention-samr21) |
| Node has only a `fe80::` address, never `2001:db8::` | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| `error getting manifest` + node cannot ping the CoAP server | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| Intermittent SLIP breakage that looks like a flaky radio | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| `sliptty: Unknown packet type 0x??` + binary garbage | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| BR shell answers nothing, `uhcp_client(): no reply received` | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| `Error connecting DP: cannot read IDR` (SWD flash) | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| OpenOCD `DEPRECATED! use 'bcm2835gpio peripheral_base'` | [Harmless](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| Radio-mode dongle boots but prints nothing | [Mesh mode](#mesh-mode-802154--raspberry-pi-device_802154_pimd) |
| `uhcpd: not found` | [Host tools](#build--toolchain) |
| Nondeterministic AEAD tag failures on native | [Worker stack on native](#worker-stack-overflow-on-native) |
| Dongle freezes on `ifconfig` or stalls mid-update | [CDC-ACM stdio blocks](#networking--terminals) |
| Dongle console dies after a reset and never returns | [CDC-ACM re-enumeration](#networking--terminals) |

---

## Expected behaviour that looks like a bug

- **`res=-5` on re-notify is correct.** The manifest sequence number is a
  strict monotonic anti-rollback counter. Re-sending an already-installed
  manifest *must* be rejected:
  ```
  Manifest seq_no: 1784305079, highest available: 1784305079
  seq_nr <= running image
  suit_worker: suit_parse() failed. res=-5
  ```
  Publish with a fresh, larger `APP_VER=$(date +%s)` to update again.

- **`suit/notify` erroring or hanging on the host means nothing.** It
  frequently prints a network error or needs Ctrl-C even when the trigger
  arrived and the update succeeded. **Judge success on the device terminal
  only.** Repeated `suit: received URL` lines mid-download are harmless
  notify retransmissions; the running worker ignores them.

- **`verification failed … at address 0x1004` before `Programming`
  (samr21).** That is `edbg` comparing the *old* flash contents. The real
  check is the `Verification.... done.` line *after* programming.

- **`'dfu-util' programmer is not supported by this board` (dongle).**
  `dfu-util` is not in the board's `PROGRAMMERS_SUPPORTED` list, but
  `riotboot/flash-slot0` invokes it directly anyway. The download still runs.

- **A rejected manifest does not consume the sequence number.** Tamper tests
  can be repeated without republishing.

---

## Signing keys

- **The verifying public key is baked in at flash time.** Flash and
  `suit/publish` must use the same `SUIT_KEY_DIR` / `SUIT_KEY` /
  `SUIT_KEY_ALGO`. Switching keys or algorithms **requires a reflash**.

- **`suit_tool.sign - Non-library key type not implemented` means the wrong
  key file was picked up, not a missing feature.** It appears when the key
  variables were not set for that command (e.g. a fresh terminal without your
  exports), so signing fell back to `~/.local/share/RIOT/keys/default.pem` —
  which Python's `cryptography` may be unable to parse (for instance an
  ML-DSA key in OpenSSL's combined seed+expanded format instead of
  seed-only). Re-run with the key variables set inline.

- **Exports are per-terminal.** This workflow uses 3–4 shells. Every command
  in the device guides repeats `SUIT_KEY_DIR=… SUIT_KEY=…` inline for exactly
  this reason.

- **Never overwrite `default.pem`** with a non-Ed25519 key; use a dedicated
  directory per algorithm.

- ML-DSA key generation needs **OpenSSL 3.5+** with
  `-provparam ml-dsa.output_formats=seed-only` — `cryptography` cannot parse
  OpenSSL's default combined seed+expanded PKCS8 encoding. The
  `suit/genkey` target handles this for you.

---

## Encryption & keys

- **The matching rule.** Build flags decide what the device *can accept*;
  publish flags decide what is *produced*; plaintext always passes through.
  The only forbidden direction is publishing more encryption than the flashed
  image supports:

  | Mismatch | Symptom |
  |---|---|
  | encrypted payload → `SUIT_FIRMWARE_ENCRYPT=0` firmware | `Image beyond size` |
  | encrypted manifest → `SUIT_MANIFEST_ENCRYPT=0` firmware | `suit_parse() failed` |
  | wrong `_ALGO` | `recipient alg -25 != built-in -70768` |

  **Use the identical flag set on flash and publish.**

- **`SUIT_MANIFEST_ENCRYPT_ALGO` must be passed to `suit/publish` too, not
  just to the flash.** It also selects the scheme used to auto-encrypt the
  *firmware payload*. Omit it on publish and the payload gets encrypted for
  `device_x25519.pem` while the firmware expects ML-KEM — surfacing much
  later as `suit: recipient alg -25 != built-in -70768`, right after
  `riotboot_flashwrite: initializing update to target slot 1`. The manifest
  itself decrypts fine, which makes this confusing.

- **The `--key` path must match the `SUIT_KEY_DIR` used for flashing,
  exactly.** Each key directory holds its *own* `device_x25519.pem` /
  `device_mlkem768.pem` — they are distinct keypairs, not shared between
  `ed25519-keys/` and `mldsa-keys/`. Encrypting against the wrong directory's
  key gives `suit: manifest decryption failed: -213`
  (wolfSSL `MAC_CMP_FAILED_E`) with **no** preceding "CEK derivation failed"
  or "recipient alg" message.

- **Why that failure is silent: FIPS 203 implicit rejection.** A wrong (or
  tampered) ML-KEM ciphertext never produces a *decapsulation* error — it
  deterministically yields a different, wrong shared secret. The failure only
  surfaces at the final AEAD tag check. Do not add or test for an error path
  at the decapsulation step.

- **Manifest encryption is not automated.** `suit/publish` encrypts the
  payload; the manifest needs the manual `encrypt_manifest.py` step
  afterwards. Payload encryption *is* automated.

- **Keep encrypted manifest names short** (`riot.suit.enc`). The device's URL
  path buffer is 128 bytes in SUIT apps (`makefiles/suit.inc.mk` exports
  `-DCONFIG_SOCK_URLPATH_MAXLEN=128`, overriding the 64-byte default), so the
  "64-char budget" quoted in older notes is a *convention*, not a hard limit —
  a 64-char dongle path was verified fine on hardware. Short names are still
  good practice.

- **`SUIT_FIRMWARE_ENCRYPT=1` implies the manifest-encryption module** (shared
  device key + crypto), even with `SUIT_MANIFEST_ENCRYPT=0`.

- **One `.enc` artifact per device key.** These schemes encrypt for a specific
  device; there is no broadcast mode. `suit/publish` publishes only the
  `.enc` files when the flag is on — publishing plaintext alongside would
  defeat the purpose.

- **Prototype caveat:** the device private key is embedded in the firmware
  image (`suit_enc_seckey.h`). Real deployments need protected key storage.
  Neither ECDH-ES nor ML-KEM authenticates the sender; the manifest signature
  is the sole authenticity anchor.

---

## RAM limits

- **`region 'ram' overflowed by N bytes` at link time is a real, honest
  answer**, not a broken build — particularly on the samr21-xpro's 32 KB.
  ML-DSA-87 overflows by ~3.6 KB and the full-PQ combo by ~3.5 KB; see
  [FINDINGS.md](FINDINGS.md) for the measured table.

- **Link-time size analysis does not prove a build fits.** `arm-none-eabi-size`
  and `nm` only bound `.data` + `.bss` + declared stack arrays. Any code path
  calling `malloc`/`XMALLOC`, or any deep call chain, needs runtime
  verification. This burned this project once, badly — see
  [the ML-KEM heap discovery](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery).

- **Big structs go `static`, never on the worker stack** (Cortex-M0+ has no
  MPU, so overflow silently corrupts adjacent `.bss` instead of faulting).
  wolfCrypt's `MlDsaKey` embeds every verify work buffer — >10 KB — and as a
  stack local it produced a 12,364-byte stack frame and *corrupted the
  manifest buffer*, making a valid signature "fail" verification.

- **RAM storage regions on native are 2 KB**, and that limit applies to the
  *plaintext* size. A bigger test payload fails at
  `Unable to start storage backend` (res=-50) before any decryption.

- Do not add unguarded board-specific debug shell commands. An unconditional
  `printf`-heavy command adds text/RAM to *every* board building this app and
  has overflowed the samr21's budget before.

### ML-KEM memory (the big one)

- **`suit: manifest CEK derivation failed: -125`** is wolfCrypt `MEMORY_E`.
  Without `WOLFSSL_NO_MALLOC`, `wc_MlKemKey_MakeKeyWithRandom()`
  unconditionally `XMALLOC`s up to 6,144 B of scratch from the C heap at
  *runtime*. The newlib heap on a constrained board can never satisfy that.

  Fixed in-tree via `WOLFSSL_NO_MALLOC` + `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` +
  `WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM` (`pkg/wolfssl/include/user_settings.h`),
  which move the buffers to the stack where `-fstack-usage` can measure them
  (~8.5–9 KB peak).

- **The worker stack is therefore forced to 9,216 B for any `ml-kem-%`
  build** (any signing algorithm, any non-native board) in the app
  `Makefile`. The old 4 KB stack would have silently overflowed into `.bss`
  at runtime — a far worse failure than the clean `MEMORY_E` actually hit.
  Nothing to pass by hand; it is automatic.

### Worker stack overflow on native

The 4 KB `SUIT_WORKER_STACKSIZE` set for ML-DSA builds (to fit samr21) is far
too small for x86-64 stack frames once the payload path nests ML-KEM
decapsulation inside the transport callback. It silently corrupted the static
AEAD state, showing up as **nondeterministic Poly1305 tag mismatches on
correct plaintext**. The override is now scoped to non-native boards; native
uses the 3×`THREAD_STACKSIZE_LARGE` default.

---

## Digest fails but the AEAD tag passed

`suit: payload decrypted (N bytes)` succeeded but you get
`Erasing bad payload` / `res=-7`.

That combination points at the **storage write path, not the crypto**. It was
an upstream riotboot bug: RAW-mode `riotboot_flashwrite_putbytes()` wrote each
filled buffer at the *input-segment* position rather than the aligned block
start — correct only for buffer-aligned chunks, and the decryptor's
header-stripped stream starts with a 38 B chunk. Fixed in
`sys/riotboot/flashwrite.c`; **reflash with a rebuilt image** (the bootloader
itself is unaffected).

Diagnostic shortcut for the future: *tag passes + digest fails + host-side
`sha256(decrypt(.enc))` equals the manifest digest* ⇒ storage path, not crypto.

---

## No valid riotboot slot (`res=-50`)

```
suit: validated manifest version
suit_worker: suit_parse() failed. res=-50
suit_worker: update failed, hdr invalid
```

`suit_storage_get_highest_seq_no()` found no valid slot header. Everything
before this — networking, fetch, decryption, signature verification — worked.

**On the nRF52840 Dongle this is the default outcome of a plain `make
flash`**, which DFU-flashes a *monolithic* hex: the board's nrfutil recipe
hardcodes `--package=$(HEXFILE).zip` and ignores `FLASHFILE`, so riotboot's
bootloader and slot headers are never written. Use the two-stage
`riotboot_dfu` install instead — see
[DEVICE_NRF52840_DONGLE.md](DEVICE_NRF52840_DONGLE.md). Plain
`bootloaders/riotboot` cannot rescue it either: it links to flash base 0x0,
which the Nordic MBR owns and DFU protects.

---

## Flash geometry (nRF52840 Dongle)

`res=-4` with `offset does not match`: the board only reserved the
Nordic-bootloader space (`ROM_OFFSET=0x1000`, `ROM_LEN=0xdf000`) for
`PROGRAMMER=nrfutil`. A `dfu-util` slot flash otherwise used the full 1 MB
(`ROM_LEN=0x100000`) and computed `SLOT1_OFFSET=0x82000`, while the
default-`PROGRAMMER` `suit/publish` used `0x71800`.

Fixed by pinning `ROM_OFFSET`/`ROM_LEN` for `nrf52840dongle` in the app
`Makefile`, so every `PROGRAMMER` agrees. Sanity-check without touching
firmware:

```bash
make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=dfu-util \
  info-debug-variable-SLOT1_OFFSET
make -C examples/advanced/suit_update BOARD=nrf52840dongle PROGRAMMER=nrfutil \
  info-debug-variable-SLOT1_OFFSET
```

Both must print `0x71800`.

---

## Class ID (native64)

On a 64-bit host `BOARD=native` resolves to the real board **`native64`**
(make prints `using BOARD="native64" as "native" on a 64-bit system`). Two
consequences:

- The tap interface is netif **`6`**, not `5`, in `ifconfig`.
- `SUIT_CLASS_ID` embedded in the firmware is `"native64"`. When calling
  `gen_manifest.py` **manually** (bypassing `suit/publish`, which sets
  `SUIT_CLASS=$(BOARD)` for you), pass `--uuid-class native64` or the device
  rejects the manifest with `res=-4`. Check the printed
  `Comparing X to Y from manifest` line to confirm.

---

## APP_VER epoch drift

`APP_VER` defaults to `$(date +%s)`, evaluated **independently** by the parent
make and the riotboot sub-make. A few seconds' drift and the two disagree
about the slot binary's filename:

- `suit/publish` → `FileNotFoundError: … slot0.<epoch>.bin`
- dongle stage-2 flash → `dfu-util: Could not open file … slot0.<epoch>.bin`

**Always pin it once per operation**: `APP_VER=$(date +%s)` captured into a
shell variable, then passed explicitly. Needed for `suit/publish` *and* for
`riotboot/flash-slot0`.

---

## USB & WSL2

- **`usbipd attach` must be re-run after every replug, reboot, and USB
  re-enumeration.** `usbipd bind` is one-time per device. A dropped
  attachment looks exactly like a flash/code bug but is not one — check
  `lsusb` in WSL first, always.

- **The dongle has three USB identities**, and each transition drops the WSL
  attachment:

  | State | USB ID | Used for |
  |---|---|---|
  | Nordic DFU bootloader | `1209:7d00` | stage-1 `nrfutil` flash |
  | RIOT `riotboot_dfu` DFU mode | `1209:7d02` | stage-2 slot flash with `dfu-util` |
  | Running RIOT (composite) | `1209:7d01` | CDC-ACM shell + CDC-ECM network + DFU runtime |

  This is the #1 cause of "could not open port" / "device not found" here.

- **`PREFLASHER=true` when the dongle is already in DFU mode.** It skips the
  1200-baud reset touch that would otherwise re-enumerate the bootloader
  mid-flash and drop the usbip attachment.

- **`dfu-util` needs root under WSL2.** libusb cannot claim a usbip device as
  your user: `Cannot open DFU device 1209:7d02`. Use `sudo dfu-util` and
  `DFU="sudo dfu-util"` for flashing, or install a udev rule (note WSL2 does
  not always run udev, so `sudo` is the reliable fallback):
  ```bash
  echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="7d02", MODE="0666"' \
    | sudo tee /etc/udev/rules.d/99-riotboot-dfu.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```

- **Re-flashing a dongle slot needs DFU *mode*, not DFU *runtime*.** Once slot
  0 is running the device is `1209:7d00` (runtime); auto-detach cannot bridge
  the PID gap. Force it: `sudo dfu-util -e -d 1209:7d00`, re-attach WSL, then
  re-run the stage-2 flash.

- **SUIT updates need no re-attach.** `suit/notify` swaps riotboot slots
  without any DFU or USB re-enumeration; the CDC-ECM link stays up.

- **`cdc_ether` binding is a known WSL2 risk.** If
  `ls -A /sys/bus/usb/drivers/cdc_ether/*/net/` prints nothing, the WSL kernel
  did not bind the network interface and the dongle's network will not come
  up.

- **Use the EDBG USB port on the samr21-xpro**, not the "TARGET USB" port —
  only EDBG carries the debugger *and* the UART bridge.

---

## Networking & terminals

- **The dongle's `make term` does not open a serial console.** It runs the
  CDC-ECM network script (the board's `TERMPROG`). Get the RIOT shell from
  the CDC-ACM serial (`/dev/ttyACM*`, `ID_MODEL_ID=7d01`) with
  `picocom -b 115200 /dev/ttyACM0` or
  `python3 -m serial.tools.miniterm`. Never use
  `dist/tools/ethos/setup_network.sh` on the dongle — it has no UART-to-USB
  bridge, so ethos enumerates nothing.

- **On the samr21, the ethos shell *is* the board terminal.** Do not run a
  second `make term` under ethos; they fight over `/dev/ttyACM*`.

- **The dongle's console "stops working" after a reset.** Its CDC-ACM port is
  provided by the running firmware, so any reboot — DFU flash, `reset`, or the
  reboot at the end of a successful update — removes it from USB and brings it
  back, frequently on a different `ttyACM*` number. `pyterm`/`make term` do not
  reconnect. Use `/dev/serial/by-id/...` (stable across renumbering) and wrap
  `picocom` in a retry loop. The samr21's EDBG port is unaffected: it belongs to
  the onboard debugger, not to the firmware.

- **`riot0` cleanup:** always exit the board terminal *before* killing the
  `setup_network.sh` shell, or the tap interface leaks.

- **The dongle hangs mid-update, or freezes on `ifconfig`.** Not a crash —
  `printf()` is spinning. `cdc_acm_stdio.c`'s `_write()` loops
  `while (len) { n = usbus_cdc_acm_submit(...); len -= n; }`, and `submit()`
  returns **0** when its ring buffer is full, *unless* the line state is
  `DISCONNECTED` (then it discards and returns). So no terminal at all is safe,
  but a terminal that is **attached and not draining** — a dead `pyterm` that
  never dropped DTR, a stalled `picocom` — blocks the device forever inside a
  print. `ifconfig` (~600 B) and `progress_bar` (reprints per received block)
  are the usual triggers against the 128 B default buffer. Mitigations, both now
  in the app `Makefile`: `CONFIG_USBUS_CDC_ACM_STDIO_BUF_SIZE=1024` for the
  dongle, and `PROGRESS_BAR=0` to make an OTA independent of the console
  entirely. Also close stale terminals rather than leaving them attached.

- **Never disable IRQs around USB-CDC-ACM `printf`.** stdio is USB on the
  dongle; a multi-line print with interrupts disabled deadlocks the device
  (the buffer fills and USB cannot drain it). The stock `current_slot`
  command gets away with one short line under `irq_disable()` — do not copy
  that pattern for anything longer.

### Mesh mode: 802.15.4 + Raspberry Pi ([DEVICE_802154_PI.md](DEVICE_802154_PI.md))

- **A node shows only a `fe80::` address, no `2001:db8::`.** It heard no router
  advertisement. Almost always a **channel or PAN-ID mismatch** between the
  border router and the node — pass identical `DEFAULT_CHANNEL` /
  `DEFAULT_PAN_ID` to *both* builds (both Makefiles include
  `makefiles/default-radio-settings.inc.mk`, so the variables mean the same
  thing on each). Check the BR's own `ifconfig` before suspecting the node; a
  BR that never came up looks the same from the node's side.

- **`suit_worker: error getting manifest`, and a node cannot ping the CoAP
  server, while `nib route` / `nib neigh` look perfect.** The server address is
  inside the prefix the border router advertises. A node then treats it as
  *on-link on the radio*, does neighbour discovery there, and never uses its
  default route. Use the address the SLIP script puts on `sl0` —
  **`fdea:dbee:f::1`** (`dist/tools/sliptty/start_network.sh`, `TUN_GLB`) —
  which is outside `2001:db8::/64` and therefore routes properly via the BR.
  Note the ethos guides legitimately use `2001:db8::1`: there the host is on the
  *same link* as the node, with no router between them. Do not copy that address
  into the border-router topology.

- **Trailing garbage on the download URL** (`.../riot.suit.latest.bin\xef\xbf\xbd`
  in the `suit_worker: downloading` line, then `error getting manifest`). The
  example's `/suit/trigger` handler used to run `strlen()`/`"%s"` over
  `pkt->payload`, but a CoAP payload is length-delimited and **not**
  NUL-terminated, so it read past the end of the payload until it happened to
  hit a zero byte. Whether it bit you depended on the URL's length, which is why
  a short server address hid it and a longer one exposed it. Fixed in
  `coap_handler.c` by passing `pkt->payload_len` through and logging with
  `"%.*s"`. If you see this on an older image, reflash.

- **`SUIT_COAP_SERVER` is frozen into the manifest URI at publish time.**
  Changing the server address means republishing (with a fresh `APP_VER`), not
  just restarting the file server.

- **Intermittent, irreproducible SLIP breakage on a Pi 4.** `/dev/serial0`
  defaults to the *mini-UART*, whose baud rate follows the VPU core clock and
  drifts under frequency scaling. Set `dtoverlay=disable-bt` (plus
  `enable_uart=1`, no serial console) so it resolves to `ttyAMA0`. This presents
  as a flaky radio, not as a UART problem.

- **`sliptty: Unknown packet type 0x75` + binary garbage = something else is on
  the serial port.** sliptty lost SLIP frame sync because another process is
  reading `/dev/serial0`. Almost always a login getty: note that
  `dtoverlay=disable-bt` moves `serial0` from `ttyS0` to `ttyAMA0`, so
  **`serial-getty@ttyAMA0` is the one that matters** — disabling only
  `serial-getty@ttyS0` is the classic half-fix. Check with
  `sudo fuser -v /dev/serial0`, and make sure `cmdline.txt` has no
  `console=serial0,…`. The same cause, earlier in the sequence, makes the border
  router's shell look unresponsive: the getty eats its output, so `ifconfig`
  returns nothing and the board looks dead.

- **OpenOCD 0.12's `DEPRECATED!` lines are noise, not failure.** `raspi.inc.mk`
  still emits the pre-0.12 `bcm2835gpio_peripheral_base` / `bcm2835gpio_swd_nums`
  spellings; 0.12 (Bookworm) warns about each and then honours them. Confirmed
  on 0.12.0 with correct Pi 4 detection (`peripheral_base = 0xfe000000`). No
  override, no older OpenOCD. Do use `OPENOCD_DEBUG_ADAPTER=raspi` rather than
  the KW41Z-mini board file's `sysfs_gpio` default — *that* driver really was
  removed in 0.12.

- **`Error connecting DP: cannot read IDR` is usually electrical.** Confirm the
  pin assignment first with
  `make info-debug-variable-OPENOCD_ADAPTER_INIT ...` — `bcm2835gpio_swd_nums`
  is `<SWCLK> <SWDIO>` in that order, and command-line `SWCLK_PIN`/`SWDIO_PIN`/
  `SRST_PIN` override both the board file and `raspi.inc.mk` (which disagree
  with each other: 20/21 vs 21/20 — the board file wins when neither is
  overridden). Then check: pins claimed by another peripheral (`raspi-gpio get`
  must show `INPUT`, not `ALT0`/`ALT4` — I²C owns GPIO2/3, SPI1 owns
  GPIO16/20/21), a missing ground wire, BCM-number-vs-header-position confusion,
  or a clock too fast for long dupont leads — retry with
  `OPENOCD_EXTRA_INIT="-c 'adapter speed 100'"`.

- **KW41Z-mini SWD pins are relocatable, but the overrides must be on every
  OpenOCD command.** The defaults are GPIO16/20/21 (header pins 36/38/40). If
  you move them, `SRST_PIN`/`SWCLK_PIN`/`SWDIO_PIN` have to be passed to
  `flash` *and* `reset` — a `flash` that silently reverts to `num 16` while the
  board is wired elsewhere looks exactly like a dead target. Prefer pins without
  fixed pull-ups for the bidirectional SWDIO: GPIO2/GPIO3 carry
  non-disableable 1.8 kΩ pull-ups (the I²C pins), fine for SRST or SWCLK but not
  for data.

- **A radio-mode dongle with no console output.** `DONGLE_NETIF=radio` must
  still keep `stdio_cdc_acm` — the board has no UART-to-USB bridge, so without
  it the dongle enumerates and runs with a completely silent stdio. The app
  Makefile selects it unconditionally for this board; if you refactor that
  block, keep `stdio_cdc_acm`, `usbus_dfu` and the `ROM_OFFSET`/`ROM_LEN`
  pinning outside the networking branch, and verify `SLOT1_OFFSET` is unchanged
  (`0x71800`) afterwards.

- **samr21 PQ combinations are not automatically still feasible in mesh mode.**
  The matrix in [DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md) was measured with
  `stdio_ethos`; radio mode swaps that for `netdev_default` + 6LoWPAN and moves
  the 32 KB budget. Re-measure a marginal row before trusting it, and watch for
  the mute-shell symptom in [RAM limits](#ram-limits).

### Serial port contention (samr21)

Flashing while `make term`/ethos is attached floods the terminal with `$`
garbage — the EDBG UART emits raw programming noise, and the session may need
a restart. **Always close the terminal before flashing, reopen after.** Also
do not run two examples' `term` simultaneously; they all want the same port.

Which node is which: check with
`udevadm info -q property -n /dev/ttyACM1 | grep ID_MODEL` — the samr21 shows
`EDBG_CMSIS-DAP`. If an nRF52840 dongle is also plugged in, it usually claims
`/dev/ttyACM0` and the samr21 lands on `/dev/ttyACM1`.

---

## Build & toolchain

- **`uhcpd: not found`** from `setup_network.sh` ⇒ you skipped
  `make -C dist/tools/uhcpd` (and `dist/tools/ethos` for the samr21).

- **First-flash race on the samr21:** the very first `make flash` may abort
  with `make -C .../edbg/bin: No such file or directory. Stop.` while
  fetching the `edbg` flasher source. Just run the same command again.

- **Never pass module selections as make *arguments*.**
  `make USEMODULE+=usbus_dfu …` is treated by GNU make as an **override** that
  silently discards the app's own modules (libcose/suit), failing with
  `cose/sign.h: No such file`. `usbus_dfu` is already enabled automatically
  for `nrf52840dongle` in the app Makefile — you do not pass it at all.

- **Module selection must happen before `Makefile.include`.** Adding
  `USEMODULE` later silently does nothing; dependency resolution has already
  run.

- **PQ builds copy ~1.1 GB.** `pkg/local.mk` `cp -a`s the local wolfSSL
  checkout (including its `.git` and host build artifacts) into
  `build/pkg/wolfssl` on every clean/prepare — slow but correct. It also
  **skips** `pkg/wolfssl`'s own small patch set (TLSX/gettimeofday fixes),
  since local-source overrides bypass RIOT's `git am` pipeline. Neither patch
  touches the ML-DSA/ML-KEM path, but it is a real gap if something else
  needs them.

- **Never enable `wolfcrypt_curve25519` (or `wolfcrypt_ed25519`) together
  with the c25519 pkg** — symbol collision on `fprime_*` with libcose's
  c25519 backend. On-device X25519 uses the **c25519 pkg**, always.

- **Host-only wolfSSL builds need `wc_curve25519_set_rng()`**
  (`WOLFSSL_CURVE25519_BLINDING` is on by default) or the shared secret fails
  with `BAD_FUNC_ARG` (-173). Affects the standalone examples only; RIOT
  device builds take the c25519 path and never hit it.

- **ML-DSA verification must use `wc_MlDsaKey_VerifyCtx(..., NULL, 0, ...)`**
  with an empty context. Plain `wc_MlDsaKey_Verify()` is **not** interoperable
  with signatures produced by Python's `cryptography` (which `suit-tool sign`
  uses). Confirmed the hard way.

---

## Implementation-level traps

Only relevant if you modify the encryption code.

- **Byte-exact CBOR.** The HKDF `info` and the AEAD AAD must serialize
  identically on both sides. The device reuses the *received* protected-header
  bytes verbatim instead of re-encoding its own.

- **In-place decrypt is safe.** wolfCrypt's `wc_ChaCha20Poly1305_Decrypt`
  explicitly supports `out == in`, and the Poly1305 tag is computed over the
  ciphertext as it is consumed. Plaintext lands *inside* `_manifest_buf` at
  the ciphertext's offset; `suit_parse()` gets that interior pointer, no
  memmove.

- **Embed the ML-KEM 64 B seed, not the 2.4/3.2 KB expanded decapsulation
  key.** `MakeKeyWithRandom` re-expands it deterministically.

- **nanocbor cannot skip tags**, and `nanocbor_leave_container()` positions the
  parent from the *child's* cursor — so the only way to learn the exact COSE
  header length (where the ciphertext stream starts) is to walk and drain
  every nested container. That full unwind doubles as the completeness check
  for a header split across transport chunks.

- **Trailing-tag lag.** With detached AEAD content the tag is the last 16 bytes
  of the stream, at a position known only at EOF — the streaming decryptor
  must permanently withhold the 16 most recent bytes. Off-by-one here corrupts
  the final block *and* breaks authentication.

- **Storage helper contract.** The decrypt wrapper forwards *plaintext
  offsets*, so image-size checks and digest verification work unchanged; the
  final `more=0` call is forwarded only **after** `CheckTag` passes, so a bad
  stream never reaches `suit_storage_finish()`. (A failed fetch can still
  leave a decrypted prefix in the inactive slot — same upstream TODO as the
  plaintext flow.)

- **Stale shared headers in the standalone ML-KEM example:** both levels write
  the same `device_*.h`/`encrypted.h` filenames. The script rewrites them on
  every run — a 1024 pubkey against a 768 build looks like a seed-expansion
  mismatch (`BUFFER_E`, -132).

- **native `file://` fetches** go through the hostfs mount:
  `file:///nvm0/<file>` maps to `native/<file>` relative to the ELF's CWD.

---

## Finally

**This is not security-audited.** RIOT's SUIT implementation is a reference,
not a production-hardened stack, and this fork embeds device private keys in
firmware images. Do not deploy it.
