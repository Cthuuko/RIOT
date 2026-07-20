# SUIT Update — Native Board Quick Setup

Short recipe for exercising the SUIT update flow on `BOARD=native` (no
hardware required). On a 64-bit host, `native` resolves to `native64` —
this affects the tap interface number and the class ID below.

Manifest **encryption is on by default** (`SUIT_MANIFEST_ENCRYPT=1`, see
step 9 and `MANIFEST_ENCRYPTION_CHANGES.md`): the build auto-generates an
X25519 device key next to the signing key and embeds it in the firmware,
and the published manifest must then be encrypted after signing (step 6b).
For the classic unencrypted flow, build with `SUIT_MANIFEST_ENCRYPT=0` and
skip step 6b — or don't: an encryption-capable firmware also accepts
plaintext manifests (pass-through).

Firmware **payload encryption is likewise on by default**
(`SUIT_FIRMWARE_ENCRYPT=1`, see `FIRMWARE_ENCRYPTION_CHANGES.md`): the
firmware can additionally decrypt ChaCha20-Poly1305-encrypted payloads
*while they stream in* (step 6c), using the same device key. It also still
accepts plaintext payloads (pass-through), so steps 6/6b alone keep
working unchanged. Opt out with `SUIT_FIRMWARE_ENCRYPT=0`.

## 1. Prerequisites

```bash
pip3 install --user cbor2 cryptography
pip3 install --user 'aiocoap[linkheader]>=0.4.1'
```

## 2. Network setup (one-time, needs sudo)

```bash
sudo dist/tools/tapsetup/tapsetup -c
sudo ip address add 2001:db8::1/64 dev tapbr0
```

## 3. CoAP file server (keep running in its own shell)

```bash
cd RIOTBASE
aiocoap-fileserver coaproot
```

## 4. Signing key

Don't reuse `SUIT_KEY_DIR/default.pem` if it may have been regenerated as a
non-ed25519 key elsewhere (e.g. ML-DSA experiments) — `suit-tool sign` only
supports ed25519. Generate a dedicated key instead:

```bash
mkdir -p keys
dist/tools/suit/gen_key.py keys/native_ed25519.pem
```

## 5. Build and run, embedding that key

```bash
SUIT_KEY_DIR=$(pwd)/keys SUIT_KEY=native_ed25519 BOARD=native \
  make -C examples/advanced/suit_update all term
```

With encryption on (the default) this also creates the device's X25519 key
`keys/device_x25519.pem` on first build (or explicitly via the
`suit/genenckey` target) and embeds its private half in the firmware —
remember the same `SUIT_KEY_DIR` when encrypting in step 6b.

In the RIOT shell, add an address on the tap interface (interface `6` on a
64-bit host, since `native` → `native64`):

```
> ifconfig 6 add 2001:db8::2/64
```

## 6. Generate, sign, and publish a payload

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

`--uuid-class native64` must match the board name baked into the firmware
(`SUIT_CLASS_ID`, printed by `make` as `using BOARD="native64" as "native"`).
A mismatch fails with `suit_worker: suit_parse() failed. res=-4`.

## 6b. Encrypt the signed manifest (default; skip only with SUIT_MANIFEST_ENCRYPT=0)

Encrypt for the device key the firmware was built with (same
`SUIT_KEY_DIR`):

```bash
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key keys/device_x25519.pem \
  -o coaproot/suit_manifest.enc coaproot/suit_manifest.signed
```

The tool prints a Python-side decrypt self-test (`Self-test decrypt: OK`)
and the container overhead (~92 bytes). It also drops `encrypted.h` /
`plaintext.h` / `device_*.h` helper headers into the current directory
(standalone-example artifacts, safe to delete here).

**Post-quantum variant**: build with
`SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768` (or `ml-kem-1024`) and encrypt with
`examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py
--key keys/device_mlkem768.pem ...` instead — same flow, ~1144-byte
overhead, verified on native64 (see `MLKEM_ENCRYPTION_CHANGES.md`). The
firmware accepts exactly the scheme it was built for and logs a clear
`recipient alg X != built-in Y` for the other one.

## 6c. Encrypted firmware payload (optional; needs SUIT_FIRMWARE_ENCRYPT=1, the default)

To also encrypt the **payload** (not just the manifest), encrypt it for
the same device key and generate the manifest with `--enc-suffix .enc`,
which keeps digest/size computed over the *plaintext* while the URI
points at the `.enc`:

```bash
python3 examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py \
  --no-headers --key keys/device_x25519.pem \
  -o coaproot/payload.bin.enc coaproot/payload.bin

dist/tools/suit/gen_manifest.py --urlroot coap://[2001:db8::1]/ --seqnr 2 \
  --uuid-class native64 --enc-suffix .enc -o suit.tmp \
  coaproot/payload.bin:0:ram:0
```

then `create`, `sign`, and (6b) encrypt the manifest as above. The device
fetches `payload.bin.enc` (a 74-byte detached COSE_Encrypt header followed
by ciphertext and a 16-byte tag, +90 bytes total) and decrypts it
chunk-by-chunk straight into storage; the manifest's image digest is then
verified over the decrypted plaintext as usual. Mind the RAM storage
region limit (2048 B per region on native) — that limit applies to the
*plaintext* size.

## 7. Trigger the update from the RIOT shell

```
> suit fetch coap://[2001:db8::1]/suit_manifest.enc
```

(or `suit_manifest.signed` for the unencrypted flow). Expected log with
encryption:

```
suit_worker: got manifest with size 466
suit_worker: manifest decrypted (374 bytes)
suit: verifying manifest signature
...
suit_worker: update successful
```

A plaintext manifest on an encryption-capable firmware instead logs
`suit_worker: manifest not encrypted, passing through` and proceeds
normally; a tampered/wrongly-encrypted container is rejected before
parsing with `suit_worker: manifest decryption failed. res=-213`.

With an encrypted payload (step 6c) the fetch additionally logs:

```
suit: decrypting payload (header 74 bytes)
suit: payload decrypted (N bytes)
Finalizing payload store
```

A plaintext payload logs `suit: payload not encrypted, passing through`;
a tampered encrypted payload fails with `suit: payload authentication
failed` *before* the store is finalized, and the update aborts.

Tip for quick tests without networking: the vfs transport works too —
copy the manifest to `examples/advanced/suit_update/native/` and
`suit fetch file:///nvm0/<file>` (that host directory is the `/nvm0`
mount, relative to where the elf runs; keep the URL under 64 chars).

## 8. Verify

```
> storage_content .ram.0 0 64
```

Increase `--seqnr` on every subsequent manifest — it's a strict monotonic
counter.

## 9. Opting out of encryption

```bash
SUIT_MANIFEST_ENCRYPT=0 SUIT_KEY_DIR=$(pwd)/keys SUIT_KEY=native_ed25519 \
  BOARD=native make -C examples/advanced/suit_update all term
```

This drops the decryption module, its crypto dependencies, and the embedded
device key from the image entirely (~17 KB smaller on native64) —
behavior is byte-identical to the pre-encryption workflow: publish and
fetch `suit_manifest.signed`, skip step 6b. Full details:
`MANIFEST_ENCRYPTION_CHANGES.md`.

`SUIT_FIRMWARE_ENCRYPT=0` analogously drops only the payload decryptor
(~2 KB text / ~450 B RAM on native64; manifest encryption stays
available): plaintext payloads work as always, and an encrypted payload
is then rejected by the normal size/digest checks (`Image beyond size`).
Note that `SUIT_FIRMWARE_ENCRYPT=1` implies the `suit_manifest_encrypt`
module (shared crypto + device key), even with `SUIT_MANIFEST_ENCRYPT=0`.
