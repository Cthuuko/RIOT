# Crypto operations — command and code location for each step

One page per crypto operation in the SUIT update flow: **the command that
performs it**, **the source that implements it**, and **how it varies per
algorithm**. It complements the other docs rather than replacing them:

| For | Read |
|---|---|
| What a manifest/slot/tier *is* | [GUIDE.md](GUIDE.md), [CRYPTO_TIERS.md](CRYPTO_TIERS.md) |
| The narrative walkthrough | [SETUP_COMMON.md](SETUP_COMMON.md) + a `DEVICE_*.md` |
| Why something failed | [GOTCHAS.md](GOTCHAS.md) |
| **Which command, which file, per operation** | **this page** |

### Who does what

The three "receiving side" operations happen **on the device**, not on the
CoAP server. `aiocoap-fileserver` (or the Pi in mesh mode) only serves bytes
over CoAP; it holds no keys and performs no crypto. All decryption and
signature verification runs inside the RIOT firmware.

```
HOST (your laptop)                      DEVICE (RIOT node)
  keygen  ─────────────────────────────►  public key + device privkey baked
  gen_manifest → suit-tool create              into the image at flash time
  suit-tool sign            ┐
  encrypt_manifest.py       ├─ sign, THEN encrypt
  encrypt_firmware.py       ┘
         │                                decrypt manifest  →  verify sig
         └── CoAP server (dumb) ──────►    →  fetch payload, decrypt streaming
```

> **Order:** sign-then-encrypt on the host, decrypt-then-verify on the
> device. The signature covers the *plaintext* manifest, so encryption
> never weakens authenticity. Neither X25519-ECDH-ES nor ML-KEM
> authenticates the sender — the manifest signature is the sole
> authenticity anchor.

---

## 0. Quick reference

| # | Operation | Side | Command / entry point | Implementation |
|---|---|---|---|---|
| 1 | Signing keypair | host | `make suit/genkey` | `makefiles/suit.base.inc.mk:82-126` |
| 2 | Device encryption keypair | host | `make suit/genenckey` | `makefiles/suit.base.inc.mk:162-173` |
| 3 | Firmware build (keys → image) | host | `make all` / `make flash` | `examples/advanced/suit_update/Makefile:48-58`, `makefiles/suit.base.inc.mk:121-124`, `:168-171` |
| 4 | Manifest generation | host | `gen_manifest.py` → `suit-tool create` | `dist/tools/suit/gen_manifest.py`, `suit_tool/create.py` |
| 5 | Manifest signing | host | `suit-tool sign` | `suit_tool/sign.py:69-156` |
| 6 | Manifest encryption | host | `manifest-encryption*/encrypt_manifest.py` | those scripts |
| 7 | Payload encryption | host | `firmware-encryption/encrypt_firmware.py` | that script; wired in `makefiles/suit.inc.mk:51-58` |
| 8 | Manifest decryption | device | `suit_manifest_decrypt()` | `sys/suit/encrypt/decrypt.c:403` |
| 9 | Signature verification | device | `_auth_handler()` | `sys/suit/handlers_envelope.c:154` |
| 10 | Payload decryption | device | `suit_payload_decrypt_helper()` | `sys/suit/encrypt/payload_decrypt.c:133` |

All host commands below assume the repo root as CWD and these exports:

```bash
cd ~/masterthesis/RIOT
export SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/<algo>-keys
export SUIT_KEY=<basename>          # e.g. ed25519, mldsa65
export SUIT_KEY_ALGO=<algo>         # omit for the ed25519 default
```

---

# Host side

## 1. Generating the signing keypair

The **public** half is compiled into the firmware; the device accepts only
manifests signed by that exact key. Change key or algorithm ⇒ reflash.

### 1.1 Per-algorithm parameters

| `SUIT_KEY_ALGO` | `openssl genpkey -algorithm` | Extra args | COSE `alg` | Pubkey | Signature | Envelope¹ |
|---|---|---|---|---|---|---|
| `ed25519` (default) | `ed25519` | — | `EdDSA` (−8) | 32 B | 64 B | 312 B |
| `es256` | `ec` | `-pkeyopt ec_paramgen_curve:P-256` | `ES256` (−7) | 64 B | 64 B | 312 B |
| `es384` | `ec` | `-pkeyopt ec_paramgen_curve:P-384` | `ES384` (−35) | 96 B | 96 B | 361 B |
| `es512` | `ec` | `-pkeyopt ec_paramgen_curve:P-521` | `ES512` (−36) | 132 B | 132 B | 413 B |
| `ml-dsa-44` | `ml-dsa-44` | `-provparam ml-dsa.output_formats=seed-only` | `ML-DSA-44` (−48) | 1312 B | 2420 B | 2672 B |
| `ml-dsa-65` | `ml-dsa-65` | `-provparam ml-dsa.output_formats=seed-only` | `ML-DSA-65` (−49) | 1952 B | 3309 B | 3561 B |
| `ml-dsa-87` | `ml-dsa-87` | `-provparam ml-dsa.output_formats=seed-only` | `ML-DSA-87` (−50) | 2592 B | 4627 B | 4879 B |

¹ measured signed-envelope size for a one-component manifest — the
Ed25519/ES256 baseline is 312 B, so ML-DSA-87 is ~15× larger on the wire.

> **ES512 signs on P-521.** The COSE name follows the hash, not the curve.
> Its 521-bit coordinates occupy **66** bytes each; a `key_size // 8`
> assumption yields 65 and produces signatures every verifier rejects —
> handled in `suit_tool/sign.py:46-55`.

> **ML-DSA needs OpenSSL 3.5+ and seed-only output.** OpenSSL's default
> combined seed+expanded PKCS8 encoding cannot be parsed by Python's
> `cryptography`. `SUIT_KEY_GENPKEY_ARGS` (`makefiles/suit.base.inc.mk:40-42`)
> adds the provparam automatically.

### 1.2 Command — the Make target (preferred)

Applies the curve/provparam workarounds for you:

```bash
mkdir -p examples/advanced/suit_update/mldsa-keys
echo 0 | SUIT_KEY_ALGO=ml-dsa-65 \
  SUIT_KEY_DIR=$(pwd)/examples/advanced/suit_update/mldsa-keys \
  SUIT_KEY=mldsa65 BOARD=samr21-xpro \
  make -C examples/advanced/suit_update suit/genkey
```

`echo 0 |` answers the interactive "encrypt the private key file?" prompt
with *none*; choose `1` for AES-256-CBC and pass `SUIT_SEC_PASSWORD=` at
publish time.

### 1.3 Command — raw OpenSSL equivalent

```bash
K=examples/advanced/suit_update/mldsa-keys
openssl genpkey -algorithm ed25519                          -out $K/ed25519.pem
openssl genpkey -algorithm ec -pkeyopt ec_paramgen_curve:P-521 -out $K/es512.pem
openssl genpkey -algorithm ml-dsa-65 \
        -provparam ml-dsa.output_formats=seed-only          -out $K/mldsa65.pem
```

Ed25519 only, no OpenSSL needed: `dist/tools/suit/gen_key.py <file> [password]`.

### 1.4 Public key → firmware header

Done automatically by the build (§3); run by hand to inspect:

```bash
openssl pkey -inform pem -in $SUIT_KEY_DIR/$SUIT_KEY.pem \
             -outform pem -pubout -out $SUIT_KEY_DIR/$SUIT_KEY.pem.pub
dist/tools/suit/pubkey_to_header.py $SUIT_KEY_DIR/$SUIT_KEY.pem.pub
```

Emits `const uint8_t public_key[][N]`, sized from the raw key material — no
per-algorithm DER offsets. Landing spot: `$(BINDIR)/riotbuild/public_key.h`.

| Concern | Location |
|---|---|
| Key-generation rule + prompt | `makefiles/suit.base.inc.mk:82-103` |
| `suit/genkey` target | `makefiles/suit.base.inc.mk:126` |
| Curve / provparam derivation | `makefiles/suit.base.inc.mk:27-42` |
| `.pem` → `.pem.pub` rule | `makefiles/suit.base.inc.mk:111-112` |
| Pubkey → C header | `makefiles/suit.base.inc.mk:121-124`, `dist/tools/suit/pubkey_to_header.py` |
| Ed25519-only generator | `dist/tools/suit/gen_key.py` |

---

## 2. Generating the device encryption keypair

Separate from the signing key, and **per key directory** —
`ed25519-keys/device_mlkem768.pem` and `mldsa-keys/device_mlkem768.pem` are
different keypairs. The **private** half is embedded in the firmware
(prototype-grade key storage).

| `SUIT_MANIFEST_ENCRYPT_ALGO` | File | `openssl genpkey -algorithm` | Extra args | COSE recipient alg |
|---|---|---|---|---|
| `x25519` (default) | `device_x25519.pem` | `x25519` | — | ECDH-ES+HKDF-256 (−25) |
| `ml-kem-768` | `device_mlkem768.pem` | `ml-kem-768` | `-provparam ml-kem.output_formats=seed-only` | −70768 (private use) |
| `ml-kem-1024` | `device_mlkem1024.pem` | `ml-kem-1024` | `-provparam ml-kem.output_formats=seed-only` | −70769 (private use) |

```bash
# Make target (applies the provparam workaround)
SUIT_MANIFEST_ENCRYPT_ALGO=ml-kem-768 SUIT_KEY_DIR=$SUIT_KEY_DIR \
  BOARD=samr21-xpro make -C examples/advanced/suit_update suit/genenckey

# raw equivalent
openssl genpkey -algorithm ml-kem-768 \
  -provparam ml-kem.output_formats=seed-only -out $SUIT_KEY_DIR/device_mlkem768.pem

# private key → embedded header (the build does this, §3)
dist/tools/suit/enckey_to_header.py $SUIT_KEY_DIR/device_mlkem768.pem
```

`enckey_to_header.py` emits `const uint8_t suit_enc_seckey[N]` — 32 B for
X25519, 64 B for the FIPS 203 seed — into
`$(BINDIR)/riotbuild/suit_enc_seckey.h`.

| Concern | Location |
|---|---|
| Key rule, header rule, `suit/genenckey` | `makefiles/suit.base.inc.mk:155-174` |
| Algo → filename mapping | `makefiles/suit.base.inc.mk:157` |
| Seed-only provparam | `makefiles/suit.base.inc.mk:153` |

---

## 3. Building the firmware

The step that binds the crypto configuration into the image. Everything above
produces key material on disk; this is where it stops being a host-side file
and becomes something the device will enforce.

It consumes the two generated headers, both pulled in via `BUILDDEPS` and both
marked `FORCE` so switching keys is picked up even when the new key file's
mtime is older:

```
$(BINDIR)/riotbuild/public_key.h        ← §1.4, the verifying public key(s)
$(BINDIR)/riotbuild/suit_enc_seckey.h   ← §2, the device private key
                                          (only with suit_manifest_encrypt)
```

```bash
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY SUIT_KEY_ALGO=$SUIT_KEY_ALGO \
  SUIT_MANIFEST_ENCRYPT_ALGO=x25519 \
  BOARD=<board> make -C examples/advanced/suit_update all
```

### 3.1 What each flag fixes in the image

| Flag | Decides |
|---|---|
| `SUIT_KEY_ALGO` | on-device verifier module (`suit_algo_es256/384/512`, `suit_algo_mldsa44/65/87`, or the Ed25519 default), `SUIT_MANIFEST_BUFSIZE`, `SUIT_WORKER_STACKSIZE`, and whether the local wolfSSL checkout is used (`PKG_SOURCE_LOCAL_WOLFSSL`) |
| `SUIT_KEY` / `SUIT_KEY_DIR` | which public key(s) get baked in — `SUIT_KEY` may list several, and the firmware accepts a manifest signed by any of them (§10) |
| `SUIT_MANIFEST_ENCRYPT` / `_ALGO` | the `suit_manifest_encrypt` module, the recipient scheme, which `device_*.pem` is embedded, and a further `SUIT_MANIFEST_BUFSIZE` bump for the COSE_Encrypt wrapper |
| `SUIT_FIRMWARE_ENCRYPT` | the `suit_firmware_encrypt` module (implies manifest encryption — `sys/suit/Makefile.dep`) |

This is the **"what the device can accept"** half of the matching rule; the
publish flags (§4-§7) decide what is actually *produced*. Plaintext always
passes through, so the only forbidden direction is publishing more than the
flashed image supports — see [SETUP_COMMON.md §2.1](SETUP_COMMON.md).

> **`SUIT_KEY_ALGO` picks the verifier, but nothing checks that
> `$(SUIT_KEY_DIR)/$(SUIT_KEY).pem` actually *is* that algorithm.** The host
> tooling reads the algorithm out of the PEM instead (`suit_tool/sign.py`'s
> `isinstance` dispatch, `pubkey_to_header.py`'s raw key bytes), while the
> firmware takes it from the Make variable. A stale key of the wrong type
> therefore builds and links cleanly and fails only on the device. This is why
> [SETUP_COMMON.md §3](SETUP_COMMON.md) insists on a dedicated key directory
> per algorithm.

### 3.2 Where it lives

| Concern | Location |
|---|---|
| Module selection from `SUIT_KEY_ALGO` | `examples/advanced/suit_update/Makefile:48-57` |
| Buffer / worker-stack sizing | `examples/advanced/suit_update/Makefile:58-86` (ML-DSA), `:100-164` (ML-KEM), `:166-180` (emission) |
| Pubkey header (declaration + rule) | `makefiles/suit.base.inc.mk:75-78`, `:121-124` |
| Device-key header (declaration + rule) | `makefiles/suit.base.inc.mk:159-160`, `:168-171` |
| Backend selection | `sys/suit/Makefile.dep:5-28`, `pkg/libcose/Makefile.dep` |

---

## 4. Generating the manifest

Two steps: a JSON template, then CBOR encoding. **Algorithm-independent** —
nothing here knows which signature scheme comes next.

```bash
dist/tools/suit/gen_manifest.py \
  --urlroot coap://[2001:db8::1]/fw/suit_update/native64 \
  --seqnr $(date +%s) \
  --uuid-vendor riot-os.org \
  --uuid-class native64 \
  -o suit.tmp.json \
  slot0.bin:0x1000 slot1.bin:0x9000

dist/tools/suit/suit-manifest-generator/bin/suit-tool create \
  -f suit -i suit.tmp.json -o riot.suit_unsigned.bin
```

| Flag | Effect |
|---|---|
| `--uuid-class` | must equal the firmware's `SUIT_CLASS_ID` (= `BOARD`), else `res=-4`. On 64-bit hosts `native` builds as **`native64`** |
| `--seqnr` | strict monotonic anti-rollback counter (= `APP_VER`) |
| `--enc-suffix .enc` | appends `.enc` to the URI **only** — digest and size stay over the plaintext |
| `slotfile:offset:name` | offset must match the riotboot slot, else `res=-4` |

`-f json` instead of `-f suit` prints the canonical manifest as JSON (with
an empty `authentication-wrapper`) — useful for diffing templates.

| Concern | Location |
|---|---|
| Template generator | `dist/tools/suit/gen_manifest.py` |
| Vendor/class UUID5 derivation | `dist/tools/suit/gen_manifest.py:49-50` |
| `.enc` URI suffix | `dist/tools/suit/gen_manifest.py:76-78` |
| JSON → CBOR encoder | `suit_tool/create.py`, `suit_tool/manifest.py` |
| Make rule | `makefiles/suit.inc.mk:62-72` |

---

## 5. Signing the manifest

```bash
dist/tools/suit/suit-manifest-generator/bin/suit-tool sign \
  -k $SUIT_KEY_DIR/$SUIT_KEY.pem \
  -m riot.suit_unsigned.bin \
  -o riot.suit.signed.bin
# encrypted key file: add -p "<password>"
```

Or the Make target, which does §4 + §5 together:

```bash
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY SUIT_KEY_ALGO=$SUIT_KEY_ALGO \
  BOARD=<board> APP_VER=$(date +%s) \
  make -C examples/advanced/suit_update suit/manifest
```

**The algorithm is detected from the PEM**, not passed as a flag — one
command covers all seven schemes. `sign.py` builds a `COSE_Sign1` whose
payload is the SHA-256 digest of the manifest, then signs the
`Signature1` structure.

> A wrong or non-parseable key surfaces as
> `Non-library key type not implemented` — that message means *the PEM was
> not loadable*, most often because `SUIT_KEY_DIR`/`SUIT_KEY` were dropped
> and make fell back to `~/.local/share/RIOT/keys/default.pem`.

| Concern | Location |
|---|---|
| PEM → COSE alg name | `suit_tool/sign.py:78-98` |
| ECDSA ASN.1 → fixed-width `r‖s` | `suit_tool/sign.py:47-56` |
| Ed25519 / ML-DSA raw sign | `suit_tool/sign.py:58-59` |
| Digest over the manifest | `suit_tool/sign.py:115` |
| Per-algorithm signer dispatch | `suit_tool/sign.py:136-143` |
| Make rule (+ password prompt) | `makefiles/suit.inc.mk:76-93` |

---

## 6. Encrypting the manifest

Hides update *metadata* (URIs, digests, versions). **Not automated** — run
it after publish, in the published directory, against the same
`$SUIT_KEY_DIR` the firmware was flashed with.

```bash
# X25519 (default)
python3 examples/advanced/suit_update/manifest-encryption/encrypt_manifest.py \
  --key $SUIT_KEY_DIR/device_x25519.pem \
  -o coaproot/fw/suit_update/<board>/riot.suit.enc \
  coaproot/fw/suit_update/<board>/riot.suit.latest.bin

# ML-KEM (post-quantum) — separate tool, --level picks the parameter set
python3 examples/advanced/suit_update/manifest-encryption-mlkem/encrypt_manifest.py \
  --level 768 --key $SUIT_KEY_DIR/device_mlkem768.pem \
  -o coaproot/fw/suit_update/<board>/riot.suit.enc \
  coaproot/fw/suit_update/<board>/riot.suit.latest.bin
```

Expect `Self-test decrypt: OK`. Keep the output name **`riot.suit.enc`** —
the notify URL is 61 of the device's 64-char budget with that short name.

Measured container overhead (constant per scheme, ±CBOR length encoding):

| Scheme | Overhead | Of which KEM ct / ephemeral key |
|---|---|---|
| X25519 | **92 B** | 32 B ephemeral public key |
| ML-KEM-768 | **1144 B** | 1088 B ciphertext |
| ML-KEM-1024 | **1624 B** | 1568 B ciphertext |

Scheme: ephemeral-static X25519 ECDH (or ML-KEM encapsulation) → HKDF-SHA256
→ ChaCha20-Poly1305, in an RFC 9770-style `COSE_Encrypt`. Wire format:
[manifest-encryption/README.md](manifest-encryption/README.md),
[manifest-encryption-mlkem/README.md](manifest-encryption-mlkem/README.md).

> Both tools also drop `encrypted.h` / `plaintext.h` / `device_*.h` in the
> **current directory** — standalone-example artifacts, safe to delete. Run
> them from a scratch directory if that matters.

---

## 7. Encrypting the firmware image

Hides the image itself. **This one is automated** — `suit/publish` encrypts
every payload when the firmware was built with `SUIT_FIRMWARE_ENCRYPT=1`
(the default) and passes `--enc-suffix .enc` to the manifest generator.

```bash
# what suit/publish runs for you (makefiles/suit.inc.mk:55-58)
python3 examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py \
  --no-headers --key $SUIT_KEY_DIR/device_x25519.pem \
  -o slot0.bin.enc slot0.bin
```

**The key file's type selects the scheme** — one tool, no `--level`; pass
`device_mlkem768.pem` and it emits an ML-KEM recipient. Always use
`--no-headers` for real payloads (skips the C-sample headers).

Wire format is a **detached-ciphertext** `COSE_Encrypt`:
`header ‖ ciphertext ‖ 16 B tag`, ciphertext the same length as the plaintext.

| Scheme | Header | Tag | Total overhead |
|---|---|---|---|
| X25519 | 74 B | 16 B | 90 B |
| ML-KEM-768 | 1126 B | 16 B | 1142 B |
| ML-KEM-1024 | 1606 B | 16 B | 1622 B |

> The tool's banner always prints `X25519 …` regardless of key type —
> cosmetic only; the header size (74 vs 1126 vs 1606) tells you what was
> actually used.

> **The manifest's `image-digest` and `image-size` stay over the
> plaintext**, because the device stores decrypted bytes. Only the URI
> gains `.enc`. That is why `--enc-suffix` exists.

| Concern | Location |
|---|---|
| Encryptor | `examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py` |
| Key-type → recipient dispatch | `.../encrypt_firmware.py:161-178` |
| `%.enc` Make rule | `makefiles/suit.inc.mk:55-58` |
| `--enc-suffix` wiring | `makefiles/suit.inc.mk:52`, `:68` |

---

## 8. Everything at once

```bash
APP_VER=$(date +%s)      # pin it: parent make and riotboot sub-make each
                         # evaluate $(date +%s) independently otherwise
SUIT_KEY_DIR=$SUIT_KEY_DIR SUIT_KEY=$SUIT_KEY SUIT_KEY_ALGO=$SUIT_KEY_ALGO \
  SUIT_MANIFEST_ENCRYPT_ALGO=x25519 \
  BOARD=<board> APP_VER=$APP_VER SUIT_COAP_SERVER=[2001:db8::1] \
  make -C examples/advanced/suit_update suit/publish
```

§3 → §4 → §5 → §7 → copies into `coaproot/fw/suit_update/<board>/`.
Then §6 by hand if you want manifest confidentiality, then `suit/notify`.

**Use the identical flag set on `flash` and on `publish`.** Build flags
decide what the device *accepts*; publish flags decide what is *produced*;
plaintext always passes through. See the matching rule in
[SETUP_COMMON.md §2.1](SETUP_COMMON.md).

---

# Device side

Everything below runs inside the RIOT firmware, in the SUIT worker thread.
The order is **decrypt → verify**, the mirror image of the host.

Entry point for all three: `sys/suit/transport/worker.c:128`
(`suit_handle_manifest_buf()`).

## 9. Decrypting the manifest

Module `suit_manifest_encrypt`, on by default; `SUIT_MANIFEST_ENCRYPT=0`
removes the module, its crypto, and the embedded device key entirely.

Decryption happens **in place in the manifest buffer, before
`suit_parse()`**. A buffer that does not start with a `COSE_Encrypt` tag
passes through untouched, so plaintext manifests keep working.

```
worker.c:128  suit_handle_manifest_buf()
  └─ decrypt.c:403  suit_manifest_decrypt()          → 0 = decrypted, 1 = plaintext
       ├─ decrypt.c:158  suit_cose_encrypt_parse()   parse container
       ├─ decrypt.c:92   _check_recipient_alg()      -25 / -70768 / -70769 vs built-in
       ├─ decrypt.c:325/362  suit_cose_derive_cek()  ML-KEM decaps | X25519 ECDH → HKDF
       ├─ decrypt.c:297  suit_cose_build_enc_structure()   AAD ("Encrypt0" structure)
       └─ ChaCha20-Poly1305 open, in place
  └─ suit_parse()  (§10 runs from inside this)
```

| Concern | Location |
|---|---|
| Call site + pass-through logging | `sys/suit/transport/worker.c:128-151` |
| Public API | `sys/include/suit/manifest_encrypt.h:62` |
| Container parse / CEK / AEAD | `sys/suit/encrypt/decrypt.c` |
| X25519 backend | **c25519 pkg** — never `wolfcrypt_curve25519` (`fprime_*` symbol collision with libcose's c25519 backend) |
| ML-KEM backend | wolfCrypt `MlKemKey`, static `_mlkem_state` at `decrypt.c:322` |
| HKDF + ChaCha20-Poly1305 | wolfCrypt (`wolfcrypt_hmac`, `wolfcrypt_chacha`, `wolfcrypt_poly1305`) |
| Module dependencies | `sys/suit/Makefile.dep:40-53` |
| Buffer sizing per algo | `examples/advanced/suit_update/Makefile:97-167` |

Failure modes: `-213` = AEAD tag mismatch (wrong device key, or tampered);
`recipient alg -25 != built-in -70768` = algo mismatch between publish and
build; `-125` = wolfCrypt `MEMORY_E`.

> **ML-KEM builds need a 9216 B worker stack**, enforced in
> `examples/advanced/suit_update/Makefile:123` for any non-native board.
> wolfCrypt's `wc_mlkem.c` allocates multi-KB scratch buffers that are
> invisible to `size`/`nm`; `WOLFSSL_NO_MALLOC` +
> `WOLFSSL_MLKEM_*_SMALL_MEM` move them to the stack
> (`pkg/wolfssl/include/user_settings.h`). The old 4 KB stack silently
> corrupted `.bss` — see [FINDINGS.md §4](FINDINGS.md).

> **Tampered KEM ciphertext fails as an AEAD tag error, never a decaps
> error** — FIPS 203 implicit rejection returns a valid-looking wrong
> shared secret by design.

## 10. Verifying the manifest signature

Runs from the envelope handler table, over the *decrypted* bytes. Two
separate checks bind the envelope together:

```
handlers_envelope.c:213  suit_envelope_handlers[]
  ├─ :154  _auth_handler()        iterates every accepted public key
  │    └─ :87  _verify_with_key()
  │         ├─ :50   suit_get_public_key()   ← public_key.h, baked in at flash
  │         ├─ cose_sign_decode()            parse COSE_Sign1
  │         └─ :135  cose_sign_verify()      ← libcose backend, per algorithm
  └─ :173  _manifest_handler()
       └─ SHA-256(manifest) vs the COSE payload digest   → -7 on mismatch
```

`SUIT_KEY` may list several keys; the firmware accepts a manifest signed by
any of them, and `_auth_handler` retries each until one verifies.

**Algorithm → on-device backend:**

| `SUIT_KEY_ALGO` | Module (app `Makefile`) | libcose backend | Crypto lib |
|---|---|---|---|
| `ed25519` | *(default)* | `libcose_crypt_c25519` | c25519 pkg |
| `es256` / `es384` / `es512` | `suit_algo_es256/384/512` | `libcose_crypt_wolfcrypt_ecdsa` | wolfCrypt ECC (+`wolfcrypt_ecc_p384`/`_p521`) |
| `ml-dsa-44/65/87` | `suit_algo_mldsa44/65/87` | `libcose_crypt_wolfcrypt_mldsa` | wolfCrypt `wc_MlDsaKey_VerifyCtx()` |

| Concern | Location |
|---|---|
| Handler table / auth / verify | `sys/suit/handlers_envelope.c:50,87,154,173,213` |
| Manifest ↔ COSE digest binding | `sys/suit/handlers_envelope.c:173-206` |
| Module selection from `SUIT_KEY_ALGO` | `examples/advanced/suit_update/Makefile:48-58` |
| Backend selection | `sys/suit/Makefile.dep:5-27`, `pkg/libcose/Makefile.dep` |
| ML-DSA backend patch | `pkg/libcose/patches/0002-cose-crypto-add-mldsa-algorithms.patch` |
| PQ scratch union (ML-DSA ∪ ML-KEM) | `pkg/libcose/patches/0003-…`, `sys/include/suit/pq_scratch.h` |
| ECDSA backend patch | `pkg/libcose/patches/0004-cose-crypto-add-ECDSA-ES256-384-512-wolfCrypt-backen.patch` |

> **ML-DSA must use `wc_MlDsaKey_VerifyCtx()` with an empty context.**
> Plain `wc_MlDsaKey_Verify()` is *not* interoperable with signatures from
> Python's `cryptography` — confirmed the hard way; see the comment in
> patch 0002.

Policy checks that run after the signature (`sys/suit/policy.c`,
`conditions.c`): sequence number > running (`-5`), vendor ID, class ID,
slot offset (`-4`), then the image digest after writing (`-7`).

## 11. Decrypting the firmware image

Module `suit_firmware_encrypt`, on by default; it **implies**
`suit_manifest_encrypt` (shared device key and primitives), so
`SUIT_MANIFEST_ENCRYPT=0` does not remove the crypto when payload
encryption is on.

Decryption is **streaming** — a wrapper callback sits in front of the
storage-write helper in the fetch directive, so the image is never held in
RAM:

```
handlers_command_seq.c:455  _dtv_fetch()
  ├─ :456  suit_payload_decrypt_start(_storage_helper)   arm the state machine
  └─ nanocoap_get_blockwise_url(..., fetch_cb = suit_payload_decrypt_helper, ...)
        └─ payload_decrypt.c:133  suit_payload_decrypt_helper()   per block
             ├─ reassemble + parse the COSE header across chunks
             ├─ derive CEK (same code path as §9)
             ├─ payload_decrypt.c:87  _feed_ciphertext()   in-place decrypt
             │     with a 16-byte trailing-tag lag (:92-97)
             └─ verify tag, then forward the final chunk to _storage_helper
```

The **tag-lag** is the crux: the last 16 bytes of the stream are the
Poly1305 tag, and they only arrive at the end — so the decryptor always
holds back 16 bytes, and never forwards the final chunk until the tag
verifies. A tampered stream is therefore never finalized.

Static header buffer, sized per scheme by `SUIT_FW_ENC_HDR_LEN`
(`sys/include/suit/pq_scratch.h:49-53`): 192 B for X25519, 1216 / 1696 B for
ML-KEM-768 / -1024 — headroom over the measured 74 / 1126 / 1606 B headers.
On full-PQ builds (ML-DSA **and** ML-KEM) it moves into the
`suit_pq_scratch` union (`sys/include/suit/pq_scratch.h:74-84`), which
overlays the ML-DSA verify state on the encryption-side states — those are
never live at the same time. Note the header buffer and the `MlKemKey` are
struct *siblings*, not union members: the buffer holds the KEM ciphertext
while it is being decapsulated.

| Concern | Location |
|---|---|
| Wrapper installation | `sys/suit/handlers_command_seq.c:449-460` |
| Streaming engine | `sys/suit/encrypt/payload_decrypt.c` |
| State machine + tag lag | `sys/suit/encrypt/payload_decrypt.c:56-131` |
| Public API + wire-format doc | `sys/include/suit/firmware_encrypt.h` |
| Module implication | `sys/suit/Makefile.dep:33-37` |
| Module selection | `examples/advanced/suit_update/Makefile:194-197` |

> **nanocbor cannot `skip` tags, and `leave_container()` needs drained
> children** — hence the full-unwind parser in `payload_decrypt.c`. Don't
> "simplify" it back.

---

# Appendix — JSON manifest examples

`suit-tool parse -j` renders a signed envelope as JSON:

```bash
dist/tools/suit/suit-manifest-generator/bin/suit-tool parse -m <manifest> -j
dist/tools/suit/suit-manifest-generator/bin/suit-tool parse -m <manifest>   # CBOR diagnostic
```

Both examples below are **real output** for the same manifest (one
component, offset 0x1000, 512 B payload), signed with two different keys.
The `manifest` object is byte-identical between them — *only the
`authentication-wrapper` changes with the algorithm*, which is exactly the
property that makes the signature axis swappable.

## A.1 EdDSA (Ed25519) — classical, complete

```json
{
  "authentication-wrapper": [
    {
      "COSE_Sign1_Tagged": {
        "protected": {
          "alg": "EdDSA"
        },
        "unprotected": {},
        "payload": {
          "algorithm-id": "sha256",
          "digest-bytes": "275f43a1b0df911aba634892f655d4f7be7851dd59111b7d74afd8aa05f9a03c"
        },
        "signature": "012a897a865540e524627829b534a495a822734632ac21b1187b776fe9d47bb995b687c50da0860cae6548569748e7b8fd5030ba05811f476692d7ff53171803"
      }
    }
  ],
  "manifest": {
    "manifest-version": 1,
    "manifest-sequence-number": 1785600000,
    "common": {
      "components": [
        ["00"]
      ],
      "common-sequence": [
        {
          "command-id": "directive-override-parameters",
          "command-arg": {
            "vendor-id": "547d0d746d3a5a9296624881afd9407b",
            "class-id": "9251dc23fd9d503f94c4bfb95f3167b1",
            "image-digest": {
              "algorithm-id": "sha256",
              "digest-bytes": "55a8607b2d99614cbcd596644e0b0a0839e36bdd09248f8ff881d19db11a510a"
            },
            "image-size": 512,
            "offset": 4096
          },
          "component-id": ["00"]
        },
        { "command-id": "condition-component-offset",   "command-arg": 5,  "component-id": ["00"] },
        { "command-id": "condition-vendor-identifier",  "command-arg": 15, "component-id": ["00"] },
        { "command-id": "condition-class-identifier",   "command-arg": 15, "component-id": ["00"] }
      ]
    },
    "install": [
      {
        "command-id": "directive-set-parameters",
        "command-arg": {
          "uri": "coap://[2001:db8::1]/fw/suit_update/native64/slot0.bin",
          "offset": 4096
        },
        "component-id": ["00"]
      },
      { "command-id": "condition-component-offset", "command-arg": 5,  "component-id": ["00"] },
      { "command-id": "directive-fetch",            "command-arg": 2,  "component-id": ["00"] },
      { "command-id": "condition-image-match",      "command-arg": 15, "component-id": ["00"] }
    ],
    "validate": [
      { "command-id": "condition-image-match", "command-arg": 15, "component-id": ["00"] }
    ]
  }
}
```

Reading the `install` sequence: set the URI and offset → assert the
component offset → fetch → assert the image matches the digest. `validate`
re-asserts the digest on every boot-time validation.

## A.2 ML-DSA-65 — post-quantum

Signature abridged; everything else is verbatim, and the `manifest` object
is identical to A.1.

```json
{
  "authentication-wrapper": [
    {
      "COSE_Sign1_Tagged": {
        "protected": {
          "alg": "ML-DSA-65"
        },
        "unprotected": {},
        "payload": {
          "algorithm-id": "sha256",
          "digest-bytes": "275f43a1b0df911aba634892f655d4f7be7851dd59111b7d74afd8aa05f9a03c"
        },
        "signature": "b0dc1f2087c4eefffb4cdea10a3b3fb199674ab0287a8beea3d57953da6f009df539f5a785865912d3a9cbf5a54831c0…00000000000000000000070e151e2429"
      }
    }
  ],
  "manifest": { "…identical to A.1…" }
}
```

The only wire-level differences, and the whole cost of going PQ on the
signature axis:

| | EdDSA | ML-DSA-65 |
|---|---|---|
| `protected.alg` | `EdDSA` (−8) | `ML-DSA-65` (−49) |
| `signature` | 64 B | **3309 B** (51×) |
| Signed envelope | 312 B | **3561 B** |
| Manifest buffer needed | 640 B | **3840 B** |
| Public key in firmware | 32 B | 1952 B |

Substituting `ML-DSA-44` (2420 B) or `ML-DSA-87` (4627 B) changes only
those numbers. For the ECDSA levels the `alg` becomes `ES256`/`ES384`/
`ES512` with 64/96/132 B signatures — all inside the default 640 B buffer.

## A.3 The input template

`gen_manifest.py`'s output — the *only* JSON you author. It carries no
signature and no algorithm; `suit-tool create` expands it into the
`common`/`install`/`validate` sequences above.

```json
{
    "manifest-version": 1,
    "manifest-sequence-number": 1785600000,
    "components": [
        {
            "install-id": ["00"],
            "vendor-id": "547d0d746d3a5a9296624881afd9407b",
            "class-id": "9251dc23fd9d503f94c4bfb95f3167b1",
            "file": "/path/to/slot0.bin",
            "uri": "coap://[2001:db8::1]/fw/suit_update/native64/slot0.bin",
            "bootable": false,
            "offset": 4096
        }
    ]
}
```

With `--enc-suffix .enc` only `uri` gains the suffix; `file` still points at
the plaintext, which is what the digest and size are computed over.

---

## Notes

- **`suit-tool parse -j` needed a fix.** It raised
  `AttributeError: 'COSETaggedAuth' object has no attribute 'cose_sign'` on
  every signed manifest: the inherited `to_json()` does an unconditional
  `getattr()` over all four tag alternatives, of which only one is ever
  set. Fixed by giving `COSETagChoice` its own `to_json()`, mirroring its
  existing `to_debug()` — `suit_tool/manifest.py:797-807`.
- **ML-KEM-1024 manifest overhead is 1624 B, not the ~1696 B quoted in
  [SETUP_COMMON.md §4](SETUP_COMMON.md).** Measured on a real container;
  the figure is consistent with ML-KEM-768's 1144 B plus the 480 B
  ciphertext difference (1568 − 1088). The 1696 B figure does match
  `SUIT_FW_ENC_HDR_LEN`, the *buffer* the device reserves — that one is
  correct, it just has headroom over the 1606 B header.
- Sizes in this document were measured on 2026-08-02 with OpenSSL 3.5.5,
  using a one-component manifest over a 512 B payload. Envelope sizes grow
  with URI length and component count; signature and key sizes do not.
