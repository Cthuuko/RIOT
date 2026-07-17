# SUIT Update — Native Board Quick Setup

Short recipe for exercising the SUIT update flow on `BOARD=native` (no
hardware required). On a 64-bit host, `native` resolves to `native64` —
this affects the tap interface number and the class ID below.

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

## 7. Trigger the update from the RIOT shell

```
> suit fetch coap://[2001:db8::1]/suit_manifest.signed
```

## 8. Verify

```
> storage_content .ram.0 0 64
```

Increase `--seqnr` on every subsequent manifest — it's a strict monotonic
counter.
