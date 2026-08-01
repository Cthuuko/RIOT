# native / native64 — SUIT updates without hardware

**Start here if you have never done this before.** The `native` board runs
RIOT as an ordinary Linux process, so you can exercise the entire SUIT flow —
manifest signing, encryption, fetch, verification — with no board, no USB, and
no flashing. Every crypto combination works here, including ones that do not
fit real hardware.

**Prerequisite:** finish [SETUP_COMMON.md](SETUP_COMMON.md) §1 (host tools)
and §3 (a signing key). New to the concepts? [GUIDE.md](GUIDE.md) first.

---

## What is different on this board

| | |
|---|---|
| **RAM** | your host's — effectively unconstrained |
| **"Flashing"** | none; `make all term` builds and runs an ELF |
| **Networking** | a `tap` interface on the host |
| **Storage** | a **memory-backed region**, not real flash — the running code is not replaced |
| **Board name** | on a 64-bit host `native` resolves to **`native64`** |

> **The `native64` renaming bites twice.** `make` prints
> `using BOARD="native64" as "native" on a 64-bit system`, and as a result:
> the tap interface is netif **`6`** (not `5`) in `ifconfig`, and the class ID
> baked into the firmware is `"native64"`. When calling `gen_manifest.py`
> manually you **must** pass `--uuid-class native64` or the device rejects the
> manifest with `res=-4`.

Because storage is memory-backed, native cannot demonstrate a real reboot into
a new slot. You verify success by reading the storage region back instead.

---

## Step 1 — Network setup (needs sudo, once per boot)

```bash
cd ~/masterthesis/RIOT
sudo dist/tools/tapsetup/tapsetup -c
sudo ip address add 2001:db8::1/64 dev tapbr0
```

## Step 2 — CoAP file server (own shell, keep running)

```bash
cd ~/masterthesis/RIOT
mkdir -p coaproot
aiocoap-fileserver coaproot
```

## Step 3 — Signing key

Do **not** reuse `default.pem` if it may have been regenerated as an ML-DSA
key elsewhere. Make a dedicated one:

```bash
mkdir -p keys
dist/tools/suit/gen_key.py keys/native_ed25519.pem

export SUIT_KEY_DIR=$(pwd)/keys
export SUIT_KEY=native_ed25519
```

(For ML-DSA keys use `suit/genkey` — see [SETUP_COMMON.md](SETUP_COMMON.md) §3.2.)

## Step 4 — Build and run

```bash
SUIT_KEY_DIR=$(pwd)/keys SUIT_KEY=native_ed25519 BOARD=native \
  make -C examples/advanced/suit_update all term
```

With encryption on (the default) the first build also creates
`keys/device_x25519.pem` and embeds its private half. Remember the same
`SUIT_KEY_DIR` when encrypting in step 6.

In the RIOT shell that opens, give the tap interface an address:

```
> ifconfig 6 add 2001:db8::2/64
```

## Step 5 — Generate, sign a payload

```bash
echo "AABBCCDD" > coaproot/payload.bin

dist/tools/suit/gen_manifest.py --urlroot coap://[2001:db8::1]/ --seqnr 1 \
  --uuid-class native64 -o suit.tmp coaproot/payload.bin:0:ram:0

dist/tools/suit/suit-manifest-generator/bin/suit-tool create -f suit \
  -i suit.tmp -o coaproot/suit_manifest

dist/tools/suit/suit-manifest-generator/bin/suit-tool sign \
  -k keys/native_ed25519.pem -m coaproot/suit_manifest \
  -o coaproot/suit_manifest.signed
```

> `--seqnr` is a strict monotonic counter — **increase it on every manifest**.

## Step 6 — Encrypt the manifest *(default flow; skip for plaintext)*

```bash
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key keys/device_x25519.pem \
  -o coaproot/suit_manifest.enc coaproot/suit_manifest.signed
```

Expect `Self-test decrypt: OK` and a ~92-byte overhead report.

ML-KEM variant: build with `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` and use
`manifest-encryption-mlkem/encrypt_manifest.py --key keys/device_mlkem768.pem`
(~1144 B overhead).

## Step 7 — Encrypt the payload too *(optional)*

Encrypt the payload for the same device key, and regenerate the manifest with
`--enc-suffix .enc` so the digest/size stay over the **plaintext** while the
URI points at the ciphertext:

```bash
python3 examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py \
  --no-headers --key keys/device_x25519.pem \
  -o coaproot/payload.bin.enc coaproot/payload.bin

dist/tools/suit/gen_manifest.py --urlroot coap://[2001:db8::1]/ --seqnr 2 \
  --uuid-class native64 --enc-suffix .enc -o suit.tmp \
  coaproot/payload.bin:0:ram:0
```

Then `create`, `sign`, and (step 6) encrypt the manifest as above.

> **RAM storage regions on native are 2 KB**, and the limit applies to the
> *plaintext* size. A bigger payload fails at `Unable to start storage backend`
> (res=-50) before any decryption.

## Step 8 — Trigger the update

In the RIOT shell:

```
> suit fetch coap://[2001:db8::1]/suit_manifest.enc
```

(or `suit_manifest.signed` for the plaintext flow). Expected:

```
suit_worker: got manifest with size 466
suit_worker: manifest decrypted (374 bytes)
suit: verifying manifest signature
suit: decrypting payload (header 74 bytes)     # only with step 7
suit: payload decrypted (N bytes)
Finalizing payload store
suit_worker: update successful
```

Pass-through and failure lines you may see instead:

| Line | Meaning |
|---|---|
| `manifest not encrypted, passing through` | plaintext manifest on an encryption-capable build — fine |
| `payload not encrypted, passing through` | plaintext payload — fine |
| `manifest decryption failed. res=-213` | tampered container or wrong device key |
| `payload authentication failed` | tampered ciphertext; aborts *before* the store is finalized |

> **No networking needed, if you prefer:** copy the manifest into
> `examples/advanced/suit_update/native/` (exported to RIOT as `/nvm0`) and
> `suit fetch file:///nvm0/<file>`. Same verification code path.

## Step 9 — Verify

```
> storage_content .ram.0 0 64
```

You should see your payload bytes. For the next update, increase `--seqnr`.

---

## Combination matrix — native / native64

Native has no RAM ceiling, so this table is about **what has been exercised**,
not what fits. Flags go on the **build** command (step 4); the manifest/payload
encryption steps (6/7) are what you vary at publish time.

Legend: ✅ verified here (built **and** run) · 🔨 **builds** — clean link
confirmed 2026-07-23, not run end-to-end · ❌ blocked

**Every row below was built from a clean `BINDIR` on 2026-07-23 and all of them
link.** On native that is a weak statement — there is no RAM ceiling — so the
`Status` column still distinguishes what has actually been *exercised* from
what merely compiles.

| # | Signature | Manifest enc | Payload enc | Build flags | Status | Steps |
|---|---|---|---|---|---|---|
| 1 | Ed25519 | — | — | `SUIT_MANIFEST_ENCRYPT=0 SUIT_FIRMWARE_ENCRYPT=0` | ✅ | 1–5, 8, 9 (skip 6, 7) |
| 2 | Ed25519 | X25519 | — | `SUIT_FIRMWARE_ENCRYPT=0` | ✅ | 1–6, 8, 9 |
| 3 | Ed25519 | — | X25519 | `SUIT_MANIFEST_ENCRYPT=0` | ✅ | 1–5, 7, 8, 9 |
| 4 | Ed25519 | X25519 | X25519 | *(none — the defaults)* | ✅ | all |
| 5 | Ed25519 | ML-KEM-768 | — | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 SUIT_FIRMWARE_ENCRYPT=0` | ✅ | 1–6†, 8, 9 |
| 6 | Ed25519 | ML-KEM-768 | ML-KEM-768 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | ✅ | all† |
| 7 | Ed25519 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | ✅ | all† |
| 8 | ML-DSA-44 | — | — | `SUIT_KEY_ALGO=ml-dsa-44` + both `=0` | ✅ signature verify | 1–5, 8‡ |
| 9 | ML-DSA-65 | — | — | `SUIT_KEY_ALGO=ml-dsa-65` + both `=0` | ✅ signature verify | 1–5, 8‡ |
| 10 | ML-DSA-87 | — | — | `SUIT_KEY_ALGO=ml-dsa-87` + both `=0` | ✅ signature verify | 1–5, 8‡ |
| 11 | ML-DSA-44 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-44` | 🔨 builds | all |
| 12 | ML-DSA-65 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-65` | 🔨 builds | all |
| 13 | ML-DSA-87 | X25519 | X25519 | `SUIT_KEY_ALGO=ml-dsa-87` | 🔨 builds | all |
| 14 | **ML-DSA-44** | **ML-KEM-768** | **ML-KEM-768** | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | ✅ **full PQ** | all† |
| 15 | ML-DSA-65 | ML-KEM-768 | ML-KEM-768 | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` | 🔨 builds | all† |
| 16 | ML-DSA-87 | ML-KEM-1024 | ML-KEM-1024 | `SUIT_KEY_ALGO=ml-dsa-87 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-1024` | 🔨 builds — max-strength | all† |
| 17 | ML-DSA-44 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_FIRMWARE_ENCRYPT=0` | 🔨 builds | 1–6, 8, 9 |
| 18 | ML-DSA-65 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-65 SUIT_FIRMWARE_ENCRYPT=0` | 🔨 builds | 1–6, 8, 9 |
| 19 | ML-DSA-87 | X25519 | — | `SUIT_KEY_ALGO=ml-dsa-87 SUIT_FIRMWARE_ENCRYPT=0` | 🔨 builds | 1–6, 8, 9 |
| 20 | ML-DSA-44 | ML-KEM-768 | — | `SUIT_KEY_ALGO=ml-dsa-44 SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 SUIT_FIRMWARE_ENCRYPT=0` | 🔨 builds | 1–6†, 8, 9 |

† Use `manifest-encryption-mlkem/encrypt_manifest.py --key
$SUIT_KEY_DIR/device_mlkem768.pem` (or `…1024`) in step 6.
‡ ML-DSA signature verification succeeds; the *full* update then hits a
**pre-existing, unrelated** component-stage failure with this branch's
`payload.bin:0:ram:0` manifest layout — the original ML-DSA-65-only code fails
identically, so it is not a regression. See
[FINDINGS.md](FINDINGS.md#7-known-limitations-and-open-items).

> **Row 14 is the interesting one.** The full post-quantum combination works
> here and on the [nRF52840 Dongle](DEVICE_NRF52840_DONGLE.md), but is
> **infeasible on the [samr21-xpro](DEVICE_SAMR21_XPRO.md)** by ~3.5 KB.
> Native's unlimited stack is exactly why native success does not prove
> hardware feasibility — see
> [FINDINGS.md §4](FINDINGS.md#4-the-wolfcrypt-ml-kem-heap-discovery).

### Opting out

```bash
SUIT_MANIFEST_ENCRYPT=0 SUIT_KEY_DIR=$(pwd)/keys SUIT_KEY=native_ed25519 \
  BOARD=native make -C examples/advanced/suit_update all term
```

Drops the decryption module, its crypto dependencies, and the embedded device
key entirely (~17 KB smaller on native64) — byte-identical behaviour to the
pre-encryption workflow. `SUIT_FIRMWARE_ENCRYPT=0` analogously drops only the
payload decryptor (~2 KB text / ~450 B RAM). Note that
`SUIT_FIRMWARE_ENCRYPT=1` **implies** the manifest-encryption module, even
with `SUIT_MANIFEST_ENCRYPT=0`.

---

## Native-specific gotchas

- **Interface number is 6, not 5**, and the class ID is `native64`. Both
  follow from the 64-bit board rename.
- **The 4 KB worker stack (set for ML-DSA builds to fit samr21) is too small
  for native** and used to corrupt the AEAD state, causing nondeterministic
  tag failures. The override is now scoped to non-native boards — but if you
  see nondeterministic crypto failures on native, this is the first suspect.
- **`file://` paths map to `native/`** relative to the ELF's working
  directory.
- **2 KB RAM storage regions**, applied to the plaintext size.

Full list: [GOTCHAS.md](GOTCHAS.md).

---

## Next

Once native works end to end, move to real hardware:
**[nRF52840 Dongle](DEVICE_NRF52840_DONGLE.md)** (easiest — no debug probe,
every combination fits) or **[samr21-xpro](DEVICE_SAMR21_XPRO.md)** (the
constrained case with the interesting negative results).
