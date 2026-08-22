# Post-quantum SUIT firmware updates on RIOT — start here

This example performs **secure over-the-air firmware updates** on embedded
devices running RIOT, following the IETF **SUIT** architecture, and extends
RIOT's stock implementation with:

- **post-quantum signatures** — ML-DSA-44/-65/-87 (FIPS 204) beside Ed25519
- **manifest encryption** — X25519 or ML-KEM-768/-1024 (FIPS 203) + ChaCha20-Poly1305
- **firmware payload encryption** — the image itself is decrypted *while it streams into flash*

If you have never done this before, read this page top to bottom (about ten
minutes), then follow the two links at the end.

---

## 1. What actually happens during an update

A SUIT update has five actors:

```
   ┌──────────┐  1. build+sign+encrypt   ┌──────────────┐
   │   you    │ ───────────────────────► │ CoAP server  │   (aiocoap-fileserver
   │  (host)  │                          │  coaproot/   │    on your laptop)
   └────┬─────┘                          └──────┬───────┘
        │ 2. "there's an update"                │
        │    (suit/notify)                      │ 3. device pulls
        ▼                                       ▼    manifest + image
   ┌──────────────────────────────────────────────────┐
   │  device: verify signature → decrypt → write to   │
   │  the *inactive* flash slot → reboot into it      │
   └──────────────────────────────────────────────────┘
```

The important part: **the device pulls, you only nudge**. `suit/notify` just
sends a URL. Everything security-relevant happens on the device.

### The manifest

A **manifest** is a small CBOR document describing the update: which device
it is for (vendor ID + class ID), a sequence number, where to fetch the
image, its SHA-256 digest, and its size. It is wrapped in a **COSE_Sign1**
envelope — that signature is the *only* thing making the update trustworthy.

The device checks, in order:

1. **Signature** against a public key baked into the firmware at flash time.
2. **Sequence number** strictly greater than the running image's — anti-rollback.
3. **Vendor / class ID** match — "is this update even for this board model?"
4. **Slot offset** match — "does this image expect to live where I'd put it?"
5. **Digest** of the downloaded image, after writing it.

Any failure aborts the update and leaves the running firmware untouched.

### Slots and riotboot

Flash is split by **riotboot** into a small bootloader plus **two application
slots**. The running firmware writes updates into the *other* slot, then
reboots; the bootloader picks whichever slot has a valid header and the
higher version. A failed update therefore cannot brick the device — the old
slot is still there.

This is why `Running from slot 1` in the terminal is the real proof of a
successful update, and why a board flashed with a *monolithic* image (no slot
headers) fails every update with `res=-50`.

### Encryption, and why it is layered on top

The stock SUIT flow gives you **authenticity and integrity** but no
**confidentiality** — anyone sniffing the network sees the manifest and the
firmware binary in the clear. This project adds two independent
confidentiality layers:

- **Manifest encryption** hides the update *metadata* (URLs, digests,
  versions). The signed manifest is wrapped in a COSE_Encrypt container and
  decrypted in place on the device *before* parsing.
- **Payload encryption** hides the *firmware image*. It ships as a detached
  COSE_Encrypt (74-byte header ‖ ciphertext ‖ 16-byte tag) and is decrypted
  chunk-by-chunk as it streams into the flash slot — the device never has to
  hold the whole image in RAM.

Both use the same scheme: a key-establishment step (X25519 ECDH or an ML-KEM
encapsulation) derives a content-encryption key via HKDF-SHA256, which keys
ChaCha20-Poly1305.

> **Order matters: sign, *then* encrypt on the host; decrypt, *then* verify
> on the device.** The signature covers the plaintext manifest, so
> confidentiality never weakens authenticity.
>
> Note also that neither X25519-ECDH-ES nor ML-KEM authenticates the
> *sender*. The manifest signature remains the sole authenticity anchor;
> encryption only adds confidentiality.

---

## 2. Why post-quantum

A sufficiently large quantum computer breaks Ed25519 and X25519 outright
(Shor's algorithm). For firmware signing this matters *today*, because
devices deployed now may still be in the field then — and a broken signature
scheme means an attacker can sign their own firmware. Encryption has the
related "harvest now, decrypt later" problem.

NIST's answers, both used here:

| Purpose | Classical | Post-quantum | Standard |
|---|---|---|---|
| Signature | Ed25519 | **ML-DSA** (Dilithium) 44/65/87 | FIPS 204 |
| Key establishment | X25519 | **ML-KEM** (Kyber) 768/1024 | FIPS 203 |

The catch, and the interesting part of this thesis: **PQC artifacts are
large**. An ML-DSA-65 signature is 3309 bytes versus Ed25519's 64 — a 50×
increase — and the verification code needs kilobytes of working memory. On a
32 KB-RAM Cortex-M0+ that is the difference between "fits comfortably" and
"does not fit at all". Which combinations fit which board is exactly what the
per-device matrices answer.

Suffix numbers are **security categories**, not versions: ML-DSA-44 = cat 2,
-65 = cat 3, -87 = cat 5; ML-KEM-768 = cat 3, -1024 = cat 5. Higher = stronger
= bigger.

---

## 3. The three axes

Every configuration in this project is one point in a 3-D space, selected by
Make variables (**defaults in bold**):

| Axis | Variable | Choices |
|---|---|---|
| Signature | `SUIT_KEY_ALGO` | **`ed25519`** · `es256` · `es384` · `es512` · `ml-dsa-44` · `ml-dsa-65` · `ml-dsa-87` |
| Manifest encryption | `SUIT_MANIFEST_ENCRYPT` (+`_ALGO`) | **`1`**/`0`; **`x25519`** · `ml-kem-768` · `ml-kem-1024` |
| Payload encryption | `SUIT_FIRMWARE_ENCRYPT` | **`1`**/`0` (reuses the manifest `_ALGO` + device key) |

Interesting corners of that space:

- **Classical baseline** — Ed25519, no encryption. RIOT's stock behaviour.
- **Hybrid** — classical signature + PQ encryption (or vice versa). Often the
  only PQ-flavoured option that fits a constrained board.
- **Full post-quantum** — ML-DSA *and* ML-KEM together. The headline result:
  **infeasible on the samr21-xpro (32 KB RAM), verified working on the
  nRF52840 Dongle (256 KB RAM)**.

Side-by-side comparison of those three regimes — artifact sizes, RAM, ROM,
threat model, workflow deltas, and which to pick:
[**CRYPTO_TIERS.md**](CRYPTO_TIERS.md).

---

## 4. Pick your board

| Board | RAM | Hardware needed | Networking | What it is good for |
|---|---|---|---|---|
| [**native / native64**](DEVICE_NATIVE.md) | host | none | tap interface | Learning the flow, testing every combination including full-PQ, no soldering iron required. Cannot really reflash itself — it updates a memory-backed storage region. |
| [**samr21-xpro**](DEVICE_SAMR21_XPRO.md) | 32 KB | board + micro-USB | ethos (serial→IP) | The **constrained** case. Real OTA to real flash. Shows exactly where PQC stops fitting — the thesis's negative results live here. |
| [**nRF52840 Dongle**](DEVICE_NRF52840_DONGLE.md) | 256 KB | ~£10 USB dongle | USB CDC-ECM | The **roomy** case. Every combination fits, including full post-quantum. No debugger needed — flashing goes over USB DFU. |

Recommended path if you are recreating this from scratch: **native first**
(prove the tooling works), then the **dongle** (easiest real hardware, no
debug probe), then the **samr21-xpro** if you want the constrained-device
results.

Those three guides are all **tethered**: one board, wired to the build host,
updates travelling over the same cable as the shell. Once a board works that
way, [**DEVICE_802154_PI.md**](DEVICE_802154_PI.md) moves it to **mesh mode** —
both real boards as untethered 802.15.4 nodes, a Raspberry Pi 4 serving the
CoAP artifacts and triggering updates through a 6LoWPAN border router. Same
manifests, same crypto, same matching rule; only the path the packets take
changes. Do it after at least one tethered board works end to end, so a failure
there is unambiguously a networking problem.

---

## 5. The documentation map

```
GUIDE.md            ← you are here: concepts, board choice
SETUP_COMMON.md     ← host prerequisites, keys, publish/notify — do this once
├── DEVICE_NATIVE.md
├── DEVICE_SAMR21_XPRO.md          each: connect → flash → network →
└── DEVICE_NRF52840_DONGLE.md      steps → FULL combination matrix
DEVICE_802154_PI.md ← mesh mode: same boards, wireless, Pi as CoAP server
GOTCHAS.md          ← every known pitfall, grouped by symptom
FINDINGS.md         ← measurements, feasibility, what was proven when
CRYPTO_TIERS.md     ← classical vs hybrid vs full PQC, side by side
CRYPTO_OPERATIONS.md ← per crypto step (incl. the firmware build): the command,
                       the source file, per algorithm
PERFORMANCE.md      ← the instrument: SUIT_PERF=1 (device), SUIT_HOST_PERF=1 (host)
├── PERF_RESULTS_NRF52840.md   ← device: verify, decapsulate, decrypt
├── PERF_RESULTS_HOST.md       ← host: sign, encapsulate, encrypt
└── PERF_RUNBOOK_NRF52840.md   ← how to capture a device run, per tier
```

**The tiers measure differently at each end**, which is the point of having
both results documents: on the device, post-quantum signature verification is
the *cheapest* option and Ed25519 the dearest; on the build host, ML-DSA
signing is the *dearest*. Everything expensive about PQC lands on the producer,
which can afford it — see
[PERF_RESULTS_HOST.md](PERF_RESULTS_HOST.md)'s headline.

Deeper references, unchanged from the implementation work:

| Document | Content |
|---|---|
| [MLDSA_HARDWARE_FIXES.md](MLDSA_HARDWARE_FIXES.md) | the three fixes that made ML-DSA-65 work on samr21 |
| [MLDSA_MULTILEVEL_CHANGES.md](MLDSA_MULTILEVEL_CHANGES.md) | generalizing ML-DSA to all three parameter sets |
| [MANIFEST_ENCRYPTION_PLAN.md](MANIFEST_ENCRYPTION_PLAN.md) / [CHANGES](MANIFEST_ENCRYPTION_CHANGES.md) | X25519 manifest encryption design + per-file changes |
| [MLKEM_ENCRYPTION_PLAN.md](MLKEM_ENCRYPTION_PLAN.md) / [CHANGES](MLKEM_ENCRYPTION_CHANGES.md) | ML-KEM variant |
| [FIRMWARE_ENCRYPTION_PLAN.md](FIRMWARE_ENCRYPTION_PLAN.md) / [CHANGES](FIRMWARE_ENCRYPTION_CHANGES.md) | streaming payload encryption |
| [manifest-encryption/](manifest-encryption/README.md), [manifest-encryption-mlkem/](manifest-encryption-mlkem/README.md), [firmware-encryption/](firmware-encryption/README.md) | standalone host-only interop demos + the COSE wire formats |
| [CRYPTO_OPERATIONS.md](CRYPTO_OPERATIONS.md) | keygen / firmware build / manifest-gen / sign / encrypt / decrypt / verify: the exact command and the exact source location for each, per algorithm, plus annotated JSON manifest examples |
| [README.native.md](README.native.md), [README.hardware.md](README.hardware.md) | upstream RIOT's original walkthroughs (classical Ed25519 only) |

---

## 6. Before you start

- **This is a research prototype, not a product.** RIOT's SUIT implementation
  is explicitly not security-audited, and this fork embeds the device's
  *private* decryption key directly in the firmware image. Real deployments
  need protected key storage.
- **Everything is driven from WSL2 (Ubuntu)** in this write-up, from VS Code
  Remote-WSL. Plain Linux works identically minus the `usbipd` steps.
- **Budget the disk.** A PQ build copies a ~1.1 GB wolfSSL checkout on every
  clean build.

Next stop: **[SETUP_COMMON.md](SETUP_COMMON.md)**, then your board's guide.
