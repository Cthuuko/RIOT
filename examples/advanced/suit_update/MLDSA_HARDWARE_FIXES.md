# ML-DSA-65 SUIT updates on real hardware (SAMR21-xpro): fixes

This documents the changes that took `SUIT_KEY_ALGO=ml-dsa-65` from
"verified on `native64` only" (see
`dist/tools/suit/ml-dsa-example/SUIT_ML_DSA_INTEGRATION.md`) to a **working
end-to-end over-the-air update on a real SAMR21-xpro** (Cortex-M0+, 32KB
RAM, 256KB flash): manifest fetched over CoAP/ethos, ML-DSA-65 signature
verified on-device by wolfCrypt, new image installed by riotboot and booted.

Verified on hardware on 2026-07-18. All three fixes below were found by
actually running the update on the board — none reproduced on `native64`.

A combined diff of all three changes is in
[mldsa-samr21-hardware-fixes.patch](mldsa-samr21-hardware-fixes.patch)
(apply from the repo root with `git apply`). The changes are also present
directly in the tree; the patch exists as a standalone record.

---

## Fix 1 — Manifest fetch buffer too small (`error getting manifest`)

**Symptom:** `suit_worker: error getting manifest` immediately after
`suit_worker: downloading ...`, on every notify.

**Cause:** `sys/suit/transport/worker.c` sizes its static manifest buffer
with `SUIT_MANIFEST_BUFSIZE`, default **640 bytes** — fine for an Ed25519
manifest (64-byte signature), but an ML-DSA-65 manifest embeds a
**3309-byte signature**, making the whole signed CBOR envelope ~3.7KB.
Both transports (`nanocoap_get_blockwise_url_to_buf()` and
`vfs_file_to_buffer()`) reject content that does not fit.

**Fix** (`examples/advanced/suit_update/Makefile`, inside the
`ifeq (ml-dsa-65,$(SUIT_KEY_ALGO))` block):

```make
CFLAGS += -DSUIT_MANIFEST_BUFSIZE=3840
```

3840 fits the ~3.75KB envelope while saving 256 bytes over a rounder 4096
on a 32KB-RAM board.

## Fix 2 — 10KB `MlDsaKey` allocated on the worker thread stack (the big one)

**Symptom (as first observed):** `suit: verifying manifest signature` →
hard fault; after enlarging the worker stack to 12KB, no more crash but
`Unable to validate signature: -2` — wolfCrypt returned `SIG_VERIFY_E`
(-229) for a **genuinely valid** signature. The identical (message,
signature, public key) triple verified fine on `native64`.

**Diagnosis:** with the `WOLFSSL_MLDSA_VERIFY_NO_MALLOC` +
`WOLFSSL_MLDSA_VERIFY_SMALL_MEM` build (the only configuration that fits
this board — the malloc path needs ~10KB of heap that isn't there),
wolfCrypt embeds *every* verify work buffer (`z`, `c`, `w`, `t1`, `w1e`,
`h`, `block`) **inside `struct MlDsaKey` itself**. The libcose backend
declared `MlDsaKey mldsa_key;` as a **stack local**, producing a 12,364-byte
stack frame (visible as `add sp, r4` with `r4 = -0x304C` in the disassembly)
on what was at most a 12KB thread stack. Cortex-M0+ has no MPU, so instead
of a clean fault the overflow silently corrupted adjacent `.bss` —
including the manifest buffer holding the signature's hint bytes — and
verification failed on corrupted input. Instrumented tracing showed
`mldsa_check_hint()` (a pure byte-wise function) "failing" on ARM for bytes
that passed on native64, which is impossible without memory corruption.

**Fix** (in `pkg/libcose/patches/0002-cose-crypto-add-mldsa65-algorithm.patch`,
i.e. libcose's `src/crypt/wolfcrypt_mldsa.c`): make the key state static —
safe because SUIT verification only ever runs on the single `suit_worker`
thread:

```c
/* With WOLFSSL_MLDSA_VERIFY_NO_MALLOC, MlDsaKey embeds every verify work
 * buffer (z/c/w/t1/w1e/h/block, >10KB on ML-DSA-65) - far too large for a
 * stack frame on a 32KB-RAM target ... */
static MlDsaKey _mldsa_key;
```

After the fix the function's stack frame is **52 bytes** (was 12,364).

## Fix 3 — RAM budget: fitting a 10KB static key state into 32KB

Making the key static moves ~12KB from (overflowing) stack to `.bss`,
which does not fit as-is. Three complementary reductions:

1. **`WOLFSSL_MLDSA_ASSIGN_KEY`** (`pkg/wolfssl/include/user_settings.h`):
   `MlDsaKey.p` becomes a `const byte *` pointer instead of a 1952-byte
   embedded copy of the public key. `wc_MlDsaKey_ImportPubRaw()` then just
   assigns the pointer — valid here because the SUIT trusted key is a
   `const` array in flash that outlives every verify call. Saves ~2KB;
   static key state shrinks to 10,368 bytes.

2. **`SUIT_WORKER_STACKSIZE=4096`** (`examples/advanced/suit_update/Makefile`):
   with the big struct out of the stack, the worker thread no longer needs
   the default 6KB (let alone 12KB); 4KB covers the CoAP fetch + SHAKE
   hashing + flashwrite path.

3. **`SUIT_MANIFEST_BUFSIZE=3840`** instead of 4096 (see Fix 1).

Resulting build for `BOARD=samr21-xpro`:

```
   text    data     bss
 106012     260   32292      (32,552 of 32,768 RAM bytes used)
```

~216 bytes of RAM to spare — tight, but stable through repeated full
updates.

---

## Files changed (all tracked in git)

| File | Change |
|---|---|
| `examples/advanced/suit_update/Makefile` | `SUIT_MANIFEST_BUFSIZE=3840`, `SUIT_WORKER_STACKSIZE=4096` under `SUIT_KEY_ALGO=ml-dsa-65` |
| `pkg/libcose/patches/0002-cose-crypto-add-mldsa65-algorithm.patch` | backend's `MlDsaKey` now `static` instead of a stack local (regenerated via `git format-patch` from the amended commit in `build/pkg/libcose`) |
| `pkg/wolfssl/include/user_settings.h` | `WOLFSSL_MLDSA_ASSIGN_KEY` added to the `MODULE_WOLFCRYPT_MLDSA` block |

**Not** in the patch: `examples/advanced/suit_update/mldsa-keys/` — the
ML-DSA-65 signing keypair generated for this board (`mldsa65.pem` is a
private key; never commit it). Regenerate with:

```sh
SUIT_KEY_ALGO=ml-dsa-65 SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=samr21-xpro make -C examples/advanced/suit_update suit/genkey
```

## How to reproduce the working update

Same flow as [HARDWARE_SAMR21_WSL.md](HARDWARE_SAMR21_WSL.md), with the
ML-DSA env exported for every make command (and, contrary to that guide's
ed25519 advice, an ML-DSA key):

```sh
export SUIT_KEY_ALGO=ml-dsa-65
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys
export SUIT_KEY=mldsa65

# flash (close the ethos terminal first - flasher and ethos share /dev/ttyACM1,
# and flashing while ethos runs floods the terminal with '$' garbage)
BOARD=samr21-xpro make -C examples/advanced/suit_update clean flash -j4

# publish + notify (fresh APP_VER pinned once, per the epoch-drift gotcha)
APP_VER=$(date +%s)
BOARD=samr21-xpro APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
SUIT_COAP_SERVER=[2001:db8::1] SUIT_CLIENT=[fe80::2%riot0] \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/notify
```

Expected on the device terminal: manifest download (size ~3.7KB), a pause
of a few seconds during `suit: verifying manifest signature` (ML-DSA-65
verify on a 48MHz M0+), firmware download progress bar, reboot into the
other slot.

## Notes / loose ends

- The device-side EDBG UART emits raw `$` bytes while the flasher is
  programming; if an ethos terminal is attached during a flash it prints
  that garbage and can need a restart. Close the terminal before flashing.
- `build/pkg/libcose` carries the amended patch commit; a `make clean`
  re-applies the (regenerated) patch from `pkg/libcose/patches/`, so tree
  and patch stay consistent.
- RAM headroom is ~216 bytes. Anything that grows `.bss` (bigger network
  buffers, more shell commands with static state) will need a compensating
  cut. If this gets too tight, candidates: shrink nanocoap's `_inbuf`
  (2KB) or the uhcp client stack.
