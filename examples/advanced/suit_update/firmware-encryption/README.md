# SUIT firmware encryption example — streaming ChaCha20-Poly1305

Standalone host-only proof of the crypto pipeline that encrypts SUIT
**firmware payloads** (as opposed to manifests — see
`../manifest-encryption/` for that sibling, whose key exchange, KDF, and
device key this reuses unchanged): the host encrypts the image for a
specific device via ephemeral-static X25519 ECDH, and the device decrypts
it **while it streams in**, because a firmware image (~120 KB on real
boards) never fits in a parse buffer the way a manifest does.

`encrypt_firmware.py` generates/loads the device's static X25519 keypair,
encrypts a payload (a built-in ~13 KB patterned test image, or any file)
into a **detached-ciphertext** COSE_Encrypt container, and self-tests by
decrypting it in Python. `wolfcrypt-sample.c` then consumes the output in
64-byte chunks — emulating the device's blockwise CoAP fetch — proving the
streaming logic the RIOT integration uses: header reassembly across chunk
boundaries, incremental AEAD, and the trailing-tag lag buffer. It also
verifies that tampered ciphertext, tag, and header are all rejected.

## Wire format

Same COSE_Encrypt (CBOR tag 96) and recipient as the manifest container,
but with **detached ciphertext** (RFC 9052 §5.1): the `ciphertext` slot is
`null` and the actual `ciphertext || tag` follows the self-delimiting CBOR
header as a raw byte stream:

```
96([
  << {1: 24} >>,           / protected: alg = ChaCha20/Poly1305        /
  {5: h'...'},             / unprotected: 12-byte nonce (IV)           /
  null,                    / ciphertext DETACHED — streams after this  /
  [[                       / one recipient                             /
    << {1: -25} >>,        / protected: alg = ECDH-ES + HKDF-256       /
    {-1: {1: 1, -1: 4,     / unprotected: ephemeral COSE_Key           /
          -2: h'...'}},    /   (kty OKP, crv X25519, x = 32-byte pub)  /
    h''                    / no encrypted key: direct key agreement    /
  ]]
])
|| ciphertext (same length as the plaintext image)
|| Poly1305 tag (16 B)
```

Key derivation and AAD are byte-identical to the manifest feature
(`CEK = HKDF-SHA256(X25519 shared, info=COSE_KDF_Context)`,
`AAD = ["Encrypt", body_protected, h'']` — see
`../manifest-encryption/README.md` for the full rules). An X25519 header
is 74 bytes; total container overhead is 90 bytes (74 + 16 tag).

## The streaming part (what's new vs. the manifest example)

- **Header reassembly**: transport chunks are 64 bytes (CoAP blockwise);
  the 74-byte header straddles a chunk boundary. Chunks accumulate in a
  192-byte buffer, re-parsed after each until the CBOR parses completely
  (truncation fails cleanly with nanocbor's ERR_END — retry; a full
  buffer that still doesn't parse is an error).
- **Exact header length**: nanocbor's `nanocbor_skip()` does *not*
  descend into tagged items, and `nanocbor_leave_container()` derives the
  parent position from the child cursor — so the parser must walk and
  **drain every nested container** to learn where the header ends and the
  ciphertext starts (see `parse_cose_header()`; this was the hard-won
  lesson of this example).
- **Incremental AEAD**: `wc_ChaCha20Poly1305_Init` →
  `_UpdateAad` → per-chunk `_UpdateData` → `_Final` + `_CheckTag`
  (present in RIOT's pinned wolfssl pkg — no wolfssl upgrade needed).
- **Trailing-tag lag**: the Poly1305 tag is the *last* 16 bytes of the
  stream, but its position is only known at end-of-stream — so the
  decryptor always withholds the 16 most recent bytes from decryption.
  Only after `_CheckTag` passes does the final chunk count as delivered;
  a tampered stream therefore never finalizes.

## Set-up

Uses the same wolfSSL checkout as the manifest-encryption example (already
configured with `--enable-curve25519` for that work — see its README for
the one-time configure/make). Python needs `cryptography` and `cbor2`.

## Run the example

```bash
python3 encrypt_firmware.py                # built-in ~13 KB test payload
python3 encrypt_firmware.py payload.bin    # or any file
```

This writes `firmware.enc` plus the C headers (`encrypted.h`,
`device_seckey.h`, `plaintext.h`) the sample embeds. Then:

```bash
gcc wolfcrypt-sample.c \
    ../../../../build/pkg/nanocbor/src/decoder.c \
    ../../../../build/pkg/nanocbor/src/encoder.c \
    -o firmware-encryption-example \
    -I../../../../build/pkg/nanocbor/include \
    -I../../../../dist/tools/suit/ml-dsa-example/wolfssl \
    -L../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs \
    -Wl,-rpath,"$(realpath ../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs)" \
    -lwolfssl
./firmware-encryption-example
```

Expected output:

```
Python-encrypted, wolfCrypt-stream-decrypted: MATCH (12828 bytes)
Tampered ciphertext byte rejected: YES
Tampered trailing tag byte rejected: YES
Tampered header nonce byte rejected: YES
```

## Use as the real host-side tool

Exactly like its manifest sibling, `encrypt_firmware.py` doubles as the
host-side encryption step of the SUIT workflow (`SUIT_FIRMWARE_ENCRYPT=1`):

```bash
python3 encrypt_firmware.py --no-headers \
    --key ~/.local/share/RIOT/keys/device_x25519.pem \
    -o coaproot/payload.bin.enc coaproot/payload.bin
```

`--no-headers` suppresses the standalone-example C headers. The manifest
is generated against the **plaintext** file (digest/size) with its URI
pointing at the `.enc` (`gen_manifest.py --enc-suffix .enc`) — the signed
image digest over the decrypted payload remains the security anchor;
decrypt-then-verify, same composition as the manifest feature.

## Security caveats (prototype!)

- Same as the manifest example: the device private key is embedded in a
  C header / the firmware image (prototype-grade key storage); ECDH-ES
  gives no sender authentication — authenticity comes from the signed
  manifest's image digest, which is verified over the *stored plaintext*
  after the fetch completes.
- On real boards the decrypted image is written to the inactive slot as
  it streams in; the slot is only validated/booted after both the AEAD
  tag and the signed digest check pass. A payload that fails either is
  erased/never installed, but its prefix has transiently existed in the
  inactive slot — same trust model as the plaintext SUIT flow.
- Per-device encryption: one `.enc` artifact per device key.
