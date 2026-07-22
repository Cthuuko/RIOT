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
| `README.md` | Upstream overview + prerequisites |
| `README.native.md` | Upstream no-hardware walkthrough (native/Linux target) |
| `README.hardware.md` | Upstream real-hardware walkthrough (ethos, border router, flashing) |
| **`GUIDE.md`** | **Entry point**: concepts primer (manifest, slots, sign-then-encrypt, why PQC), the three axes, "pick your board" table, documentation map |
| **`SETUP_COMMON.md`** | **Device-independent setup**: host prereqs, PQ prereqs, signing/device keys, the update cycle (publish → encrypt → notify), the **matching rule**, error-code table |
| **`DEVICE_NATIVE.md`** | **E2E reference**: `BOARD=native`/`native64` walkthrough + full combination matrix |
| **`DEVICE_SAMR21_XPRO.md`** | **E2E reference**: samr21-xpro over ethos + full combination matrix (the RAM-constrained board — many ❌). Supersedes `HARDWARE_SAMR21_WSL.md` + `SAMR21_EXAMPLES.md` |
| **`DEVICE_NRF52840_DONGLE.md`** | **E2E reference**: nRF52840 Dongle (riotboot_dfu two-stage install, CDC-ECM) + full combination matrix (all fit; full-PQ verified) |
| **`GOTCHAS.md`** | **All pitfalls**, grouped by symptom, with a symptom→section lookup table |
| **`FINDINGS.md`** | **All measurements/feasibility/status**: per-board RAM tables, the wolfCrypt ML-KEM heap discovery, hardware-only findings, open items |
| `NATIVE_SETUP.md`, `HARDWARE_SAMR21_WSL.md`, `SAMR21_EXAMPLES.md`, `HARDWARE_NRF52840_DONGLE_WSL.md` | Retired — one-line redirect stubs pointing at the `DEVICE_*.md` successors |
| `MANIFEST_ENCRYPTION_PLAN.md` | Manifest-encryption feature plan + status checklist — resume work from the first unchecked step |
| `MANIFEST_ENCRYPTION_CHANGES.md` | Manifest-encryption code-change summary: wire format, opt-out contract, per-file change list, verification results, gotchas |
| `manifest-encryption/` | Standalone host-only interop example (Python `cryptography` encrypt ↔ wolfCrypt decrypt); its `encrypt_manifest.py` doubles as the host-side manifest encryption tool |
| `FIRMWARE_ENCRYPTION_PLAN.md` | Firmware **payload** encryption (streaming ChaCha20-Poly1305) plan + status checklist |
| `FIRMWARE_ENCRYPTION_CHANGES.md` | Firmware-encryption code-change summary: detached-ciphertext wire format, streaming state machine, opt-out contract, measured samr21 numbers, native64 verification, gotchas (incl. the native worker-stack corruption find) |
| `firmware-encryption/` | Standalone interop example (Python encrypt ↔ wolfCrypt **streaming** decrypt in 64 B chunks); its `encrypt_firmware.py --no-headers` is the host-side payload encryption tool (auto-detects X25519 vs ML-KEM device keys) — also invoked automatically by `suit/publish` |
| `MLKEM_ENCRYPTION_PLAN.md` | Post-quantum (ML-KEM-768/1024) manifest-encryption plan + status checklist, incl. the **measured 12-combo samr21 feasibility matrix** |
| `MLKEM_ENCRYPTION_CHANGES.md` | ML-KEM code-change summary: selection contract, per-file changes, matrix, native64 verification, gotchas |
| `manifest-encryption-mlkem/` | ML-KEM standalone interop example (both levels via `--level`/`-DMLKEM_LEVEL`); its `encrypt_manifest.py --key` is the host-side ML-KEM encryption tool |
| `mlkem-feasibility-matrix-raw.txt` | Raw `size`/ld output of the 12 signing × encryption samr21 builds |
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

## E2E testing (use the reference docs)

`SETUP_COMMON.md` plus the three `DEVICE_*.md` guides are the canonical,
tested recipes for exercising the SUIT update workflow end-to-end.
**When running e2e tests, follow them step by step instead of improvising
commands**:

- `SETUP_COMMON.md` — everything board-independent: host prerequisites, key
  generation (ed25519 and ML-DSA via `suit/genkey`), device encryption keys,
  and the four-step update cycle (fileserver → `suit/publish` → optional
  manual manifest encryption → `suit/notify`). Also holds the **matching
  rule** (build flags decide what the device accepts; publish flags decide
  what is produced; plaintext always passes through) and the error-code table.
- `DEVICE_NATIVE.md` — `BOARD=native` (→ `native64` on 64-bit hosts): tap
  networking, dedicated ed25519 key, build+term, manifest
  generate/sign/publish, `suit fetch`, verification via `storage_content`;
  covers the default encrypted-manifest flow, encrypted payloads, and the
  opt-outs.
- `DEVICE_SAMR21_XPRO.md` — real `samr21-xpro` over ethos: usbipd attach,
  network bridge, flash, publish/notify, an ML-DSA-65 worked example, and the
  full combination matrix including the confirmed-infeasible rows.
- `DEVICE_NRF52840_DONGLE.md` — nRF52840 Dongle: the **two-stage
  `riotboot_dfu` install** (a plain `make flash` cannot do OTA — `res=-50`),
  CDC-ECM networking, CDC-ACM shell, and the full-PQ worked example.

`GOTCHAS.md` is the single place for failure modes — the expected `res=-5` on
seqnr replay, terminal/flash port contention, the misleading `Non-library key
type not implemented` signing error, `APP_VER` epoch drift, WSL USB
re-attachment, and the encryption key/algo mismatches. `FINDINGS.md` holds the
feasibility numbers; where an older `*_CHANGES.md` disagrees, `FINDINGS.md`
§3.1 wins.

Rules for Claude when executing these flows:

- **Never run `sudo` commands yourself.** Steps that need root
  (`tapsetup`, `ip address add`, `setup_network.sh`, `apt-get`,
  `usbipd` on the Windows side) must be handed to the user: print the
  exact command and wait for them to run it and confirm before
  continuing.
- Long-running foreground processes (`aiocoap-fileserver`, ethos/
  `setup_network.sh`, `make term`) each need their own shell — run
  non-sudo ones in the background or ask the user to keep them running in
  a separate terminal; don't block on them.
- Always bump the sequence number (`--seqnr` / fresh `APP_VER=$(date +%s)`)
  for every published manifest — it's a strict monotonic counter.
- Verify results on the device side (RIOT shell / board terminal output),
  not by the host command's exit code — `suit/notify` in particular can
  error or hang on the host even when the update succeeded.

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

> **Reader-facing versions of everything below now live in `GOTCHAS.md`
> (pitfalls, grouped by symptom) and `FINDINGS.md` (measurements and
> feasibility).** Point users at those; this section is the condensed
> in-context summary. Where the two disagree on feasibility numbers,
> `FINDINGS.md` §3.1 is authoritative.

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
- **ML-DSA (post-quantum) manifest signing AND on-device verification are
  both supported, for all three FIPS 204 parameter sets** —
  `SUIT_KEY_ALGO=ml-dsa-44|ml-dsa-65|ml-dsa-87` each select the matching
  on-device verifier (see `MLDSA_MULTILEVEL_CHANGES.md` for the multi-level
  generalization, per-level constants, and native64 test results; the rest
  of this bullet describes the original ML-DSA-65 bring-up, which all
  levels share): `SUIT_KEY_ALGO=ml-dsa-65 make -C
  examples/advanced/suit_update BOARD=native64 all` generates/signs with an
  ML-DSA-65 key (`suit/genkey`, `%.pem.pub`, `public_key.h` generation, and
  `suit-tool sign` all handle it like Ed25519 — see
  `dist/tools/suit/ml-dsa-example/README.md` for the crypto background) and
  compiles firmware that verifies it, via a new `libcose_crypt_wolfcrypt_mldsa`
  backend (`pkg/libcose/patches/0002-...patch`) calling wolfCrypt's
  `wc_MlDsaKey_VerifyCtx()` (empty context — plain `wc_MlDsaKey_Verify()` is
  **not** interoperable with signatures produced by Python's `cryptography`
  library, confirmed the hard way; see the comment in
  `pkg/libcose/patches/0002-cose-crypto-add-mldsa65-algorithm.patch`).
  Requires OpenSSL 3.5+ (for `openssl genpkey -algorithm ml-dsa-65
  -provparam ml-dsa.output_formats=seed-only` — `cryptography` can't parse
  OpenSSL's default combined seed+expanded-key PKCS8 encoding, only the
  seed-only one; `makefiles/suit.base.inc.mk`'s `SUIT_KEY_GENPKEY_ARGS`
  handles this automatically) and a `cryptography` build with ML-DSA support.
  - wolfCrypt's ML-DSA source is **not** taken from RIOT's own pinned
    `pkg/wolfssl` fetch (too old, predates wolfSSL's ML-DSA support) but from
    the local, already-`--enable-dilithium`-configured checkout at
    `dist/tools/suit/ml-dsa-example/wolfssl`, via RIOT's `PKG_SOURCE_LOCAL_*`
    package-override mechanism (`pkg/local.mk`) — set automatically by
    `makefiles/suit.base.inc.mk` when `SUIT_KEY_ALGO=ml-dsa-65`.
  - **Caveat**: `pkg/local.mk` fully `cp -a`s that local checkout (~1.1GB,
    including its own `.git` and host build artifacts) into `build/pkg/wolfssl`
    on every clean/prepare — slow, but correct — and **skips**
    `pkg/wolfssl`'s own small patch set (TLSX/gettimeofday fixes) entirely,
    since local-source overrides bypass RIOT's normal `git am` patch
    pipeline. Neither patch touches anything the ML-DSA path uses, but it's
    a real gap if something else in that tree needs them.
  - A native-only build quirk already fixed in the vendored checkout itself
    (`wolfcrypt/src/wc_port.c`, `wc_accept_cloexec()`): RIOT's own
    `posix_sockets` `<sys/socket.h>` shim shadows glibc's real header on
    native and doesn't declare `accept4()`, even with `_GNU_SOURCE` set —
    guarded out via `#if !defined(WOLFSSL_RIOT_OS) && ...`. Real (embedded)
    boards never hit this, since `__unix__` isn't defined there.
  - Verified end-to-end on `BOARD=native64` via `suit fetch
    file://<path-to-signed-manifest>` (no networking/tap needed for that
    transport) — a real `suit-tool`-signed ML-DSA-65 manifest verifies via
    `sys/suit/handlers_envelope.c`'s normal `_auth_handler`/`cose_sign_verify`
    path, and a manifest signed with a different key, or a tampered
    signature, is correctly rejected. RAM/flash feasibility on real
    constrained boards (default `samr21-xpro`, 32KB RAM) is unverified —
    `WOLFSSL_MLDSA_VERIFY_ONLY`/`_SMALL_MEM`/`_NO_MALLOC` are enabled to
    minimize footprint, but this hasn't been tested on real hardware.
- **Manifest encryption (confidentiality) is on by default** —
  `SUIT_MANIFEST_ENCRYPT=0` opts out (module, crypto, and embedded device
  key then absent entirely). Design: ephemeral-static X25519 (ECDH-ES +
  HKDF-256) + ChaCha20-Poly1305 in an RFC 9770-style COSE_Encrypt, decrypted
  in place in `sys/suit/encrypt/decrypt.c` before `suit_parse()`;
  sign-then-encrypt on the host (`manifest-encryption/encrypt_manifest.py
  --key $(SUIT_KEY_DIR)/device_x25519.pem`), decrypt-then-verify on-device.
  Plaintext manifests still pass through. Verified E2E on native64 **and
  on real samr21-xpro hardware** (2026-07-19, Ed25519+X25519: full
  encrypted OTA + reboot, mixed with plain pass-through updates —
  DEVICE_SAMR21_XPRO.md matrix row 2; watch the 64-char URL budget: the notify
  path is 61 chars with the mandatory short `riot.suit.enc` name). Key gotchas: on-device X25519 uses the
  **c25519 pkg**, never `wolfcrypt_curve25519` (symbol collision
  `fprime_*` with libcose's c25519 backend); HKDF `info`/AAD CBOR must be
  byte-exact across sides; the standalone host sample needs
  `wc_curve25519_set_rng()` (blinding default). See
  `MANIFEST_ENCRYPTION_CHANGES.md` (changes/gotchas),
  `MANIFEST_ENCRYPTION_PLAN.md` (status), `manifest-encryption/README.md`
  (wire format), `DEVICE_NATIVE.md` steps 6/9 (workflow).
  **Post-quantum variant**: `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768|ml-kem-1024`
  swaps the X25519 recipient for an ML-KEM encapsulation (compile-time
  dispatch; private-use COSE algs -70768/-70769; device key = 64B FIPS 203
  seed; needs OpenSSL 3.5+ and the local wolfssl checkout, auto-selected).
  Verified E2E on native64. **samr21 (corrected 2026-07-20 — the
  original link-time-only numbers were wrong, see below)**:
  Ed25519+ML-KEM-768/1024 fit (2.0KB / 48B RAM spare — 1024 is essentially
  zero-margin); **the full-PQ combo ML-DSA-44+ML-KEM-768 does NOT fit**
  (overflows ~3.4KB, manifest-only) despite the `suit_pq_scratch` union
  (sys/include/suit/pq_scratch.h + libcose patch 0003: the never-concurrent
  ML-DSA verify state and MlKemKey share one static allocation) and exact
  manifest buffer sizing — those savings are real but not enough once
  wolfCrypt's ML-KEM runtime stack need (~8.5-9KB, see below) is properly
  accounted for. ML-DSA-65/87+KEM don't fit either. Gotcha: tampered KEM
  ct fails via FIPS 203 implicit rejection (AEAD tag error, never a
  decaps error). **Critical gotcha (found on real hardware, not at
  link time)**: wolfCrypt's `wc_mlkem.c` unconditionally `XMALLOC`s
  multi-KB scratch buffers at runtime (up to 6,144B in one call) —
  invisible to `arm-none-eabi-size`/`nm`, so every prior "measured RAM
  feasibility" claim for ML-KEM on samr21 was link-time-only and wrong.
  Symptom: `CEK derivation failed: -125` (MEMORY_E) despite "spare RAM"
  at link time. Fixed via `WOLFSSL_NO_MALLOC` +
  `WOLFSSL_MLKEM_MAKEKEY_SMALL_MEM` + `WOLFSSL_MLKEM_ENCAPSULATE_SMALL_MEM`
  (`pkg/wolfssl/include/user_settings.h`, moves the buffers to a
  `-fstack-usage`-measured ~8.5-9KB stack instead) plus a corrected,
  enforced 9,216B `SUIT_WORKER_STACKSIZE` for any `ml-kem-%` build
  (`examples/advanced/suit_update/Makefile`, any signing algorithm, any
  non-native board) — the old 4KB stack would otherwise have silently
  corrupted `.bss` at runtime (no MPU on Cortex-M0+), a worse failure
  than the clean MEMORY_E actually hit. See
  `MLKEM_ENCRYPTION_PLAN.md` / `MLKEM_ENCRYPTION_CHANGES.md` (correction
  section) and `FIRMWARE_ENCRYPTION_CHANGES.md` (authoritative
  re-measured table).
- **Firmware payload encryption is on by default** —
  `SUIT_FIRMWARE_ENCRYPT=0` opts out. The payload ships as a
  detached-ciphertext COSE_Encrypt (74 B header ‖ ciphertext ‖ 16 B tag,
  same device key/recipient scheme as manifest encryption, which the
  module therefore implies) and is decrypted **while it streams in**
  (wolfCrypt incremental AEAD + 16-byte trailing-tag lag, wrapper in
  front of `_storage_helper` in `sys/suit/handlers_command_seq.c`;
  engine: `sys/suit/encrypt/payload_decrypt.c`). Manifest image-digest/
  size stay over the *plaintext* (`gen_manifest.py --enc-suffix .enc`);
  `suit/publish` automates payload encryption + URIs. Plaintext payloads
  pass through. Verified E2E on native64 (CoAP + VFS, tamper, opt-out,
  and the full-PQ ML-DSA-44+ML-KEM-768 combo — **native only**, see
  below); samr21 correctly measured (2026-07-20, see the ML-KEM bullet
  above for the runtime-heap discovery that forced a re-measurement):
  Ed25519+X25519 and ML-DSA-44+X25519 fit, ML-DSA-65 needs
  `SUIT_FIRMWARE_ENCRYPT=0`, Ed25519+ML-KEM-768 fits (2.0KB spare), and
  **the full-PQ combo ML-DSA-44+ML-KEM-768 does not fit** (~3.6KB short)
  despite the extended `suit_pq_scratch` union (the payload header buffer
  must never overlay the `MlKemKey`, the KEM ct lives inside it — that
  saving is real, just insufficient alone). **Key gotchas**: nanocbor
  can't `skip` tags and `leave_container` needs drained children
  (full-unwind parser); the ML-DSA builds' 4 KB worker stack overflows on
  *native* in the payload path's deeper call chain, corrupting `.bss`
  (nondeterministic tag failures — now scoped to non-native); wolfCrypt's
  ML-KEM heap-vs-stack issue above (found via this feature's samr21
  bring-up, but it's a manifest-encryption-layer bug, not specific to
  payload encryption). See `FIRMWARE_ENCRYPTION_CHANGES.md`,
  `DEVICE_NATIVE.md` step 7, `DEVICE_SAMR21_XPRO.md` matrix rows 3–7
  (row 14, the full-PQ combo, confirmed infeasible).
- `USE_ETHOS=1` by default for real hardware (serial-over-IP); set
  `USE_ETHOS=0` and use a border router instead for wireless (BLE/802.15.4) setups.
- Signing keys live in `SUIT_KEY_DIR`, default `~/.local/share/RIOT/keys` —
  auto-generated on first use, or manually via the `suit/genkey` target.
- USB/serial flashing from WSL2 requires `usbipd-win` passthrough from
  Windows. Check `lsusb` / `/dev/ttyACM*` visibility in WSL before assuming a
  flash failure is a code problem.
- Upstream README flags this SUIT implementation as not security-audited —
  don't treat it as production-ready when suggesting deployment changes.
