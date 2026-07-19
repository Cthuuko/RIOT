/*
 * Copyright (C) 2026 Miguel Arcilla
 *
 * This file is subject to the terms and conditions of the GNU Lesser
 * General Public License v2.1. See the file LICENSE in the top level
 * directory for more details.
 */

/**
 * @ingroup     sys_suit
 * @{
 *
 * @file
 * @brief       SUIT manifest decryption (COSE_Encrypt, X25519 + HKDF-SHA256 +
 *              ChaCha20-Poly1305 via wolfCrypt)
 *
 * Device-side counterpart of dist tooling's encrypt step; the wire format
 * and the byte-exact KDF-context/AAD rules are documented in
 * examples/advanced/suit_update/manifest-encryption/README.md, whose
 * standalone sample this implementation mirrors.
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 * @}
 */

#include <stdint.h>
#include <string.h>

#include "log.h"
#include "suit/manifest_encrypt.h"

/* X25519 via the c25519 pkg (dlbeer): already linked for Ed25519 manifest
 * verification (libcose_crypt_c25519), and its low-mem field arithmetic
 * shares symbol names (fprime_*) with wolfCrypt's fe_low_mem.c, so pulling
 * wolfCrypt's CURVE25519_SMALL implementation alongside it would collide at
 * link time. wolfCrypt still provides HKDF + ChaCha20-Poly1305. */
#include "c25519.h"

#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/hmac.h>
#include <wolfssl/wolfcrypt/kdf.h>
#include <wolfssl/wolfcrypt/chacha20_poly1305.h>
#include <wolfssl/wolfcrypt/error-crypt.h>

#include <nanocbor/nanocbor.h>

#define X25519_KEYSIZE  (32)

/* const uint8_t suit_enc_seckey[32]; generated into $(BINDIR)/riotbuild by
 * makefiles/suit.base.inc.mk from the SUIT_ENC_SEC X25519 key */
#include "suit_enc_seckey.h"

#define COSE_ALG_CHACHA20_POLY1305  24
#define COSE_ALG_ECDH_ES_HKDF_256  (-25)
#define COSE_TAG_ENCRYPT            96
#define COSE_HDR_IV                 5
#define COSE_KEY_PARAM_EPHEMERAL   (-1)
#define COSE_KEY_PARAM_X          (-2)

#define NONCE_LEN  CHACHA20_POLY1305_AEAD_IV_SIZE      /* 12 */
#define TAG_LEN    CHACHA20_POLY1305_AEAD_AUTHTAG_SIZE /* 16 */

/* Parsed fields of the COSE_Encrypt container (pointers into the buffer) */
typedef struct {
    const uint8_t *body_protected;      /* serialized {1: 24} */
    size_t body_protected_len;
    const uint8_t *recipient_protected; /* serialized {1: -25} */
    size_t recipient_protected_len;
    const uint8_t *nonce;               /* 12 bytes */
    const uint8_t *ciphertext;          /* without the trailing tag */
    size_t ciphertext_len;
    const uint8_t *auth_tag;            /* 16 bytes */
    const uint8_t *ephemeral_pub;       /* 32 bytes */
} cose_encrypt_msg_t;

/* Fetch the value for `wanted` from a CBOR map, skipping other entries */
static int _map_get_bstr(nanocbor_value_t *map, int32_t wanted,
                         const uint8_t **buf, size_t *len)
{
    while (!nanocbor_at_end(map)) {
        int32_t key;
        if (nanocbor_get_int32(map, &key) < 0) {
            return -1;
        }
        if (key == wanted) {
            return nanocbor_get_bstr(map, buf, len) < 0 ? -1 : 0;
        }
        if (nanocbor_skip(map) < 0) {
            return -1;
        }
    }
    return -1;
}

static int _parse_cose_encrypt(const uint8_t *buf, size_t len,
                               cose_encrypt_msg_t *msg)
{
    nanocbor_value_t it, body, map, recipients, recipient, key_map;
    uint32_t tag;
    const uint8_t *ct;
    size_t ct_len, eph_len, nonce_len;

    nanocbor_decoder_init(&it, buf, len);
    if (nanocbor_get_tag(&it, &tag) < 0 || tag != COSE_TAG_ENCRYPT) {
        return -1;
    }
    if (nanocbor_enter_array(&it, &body) < 0 ||
        nanocbor_get_bstr(&body, &msg->body_protected,
                          &msg->body_protected_len) < 0) {
        return -1;
    }
    if (nanocbor_enter_map(&body, &map) < 0 ||
        _map_get_bstr(&map, COSE_HDR_IV, &msg->nonce, &nonce_len) < 0 ||
        nonce_len != NONCE_LEN) {
        return -1;
    }
    nanocbor_leave_container(&body, &map);
    if (nanocbor_get_bstr(&body, &ct, &ct_len) < 0 || ct_len < TAG_LEN) {
        return -1;
    }
    msg->ciphertext = ct;
    msg->ciphertext_len = ct_len - TAG_LEN;
    msg->auth_tag = ct + ct_len - TAG_LEN;

    /* single recipient: [protected, {-1: COSE_Key}, h''] */
    if (nanocbor_enter_array(&body, &recipients) < 0 ||
        nanocbor_enter_array(&recipients, &recipient) < 0 ||
        nanocbor_get_bstr(&recipient, &msg->recipient_protected,
                          &msg->recipient_protected_len) < 0) {
        return -1;
    }
    if (nanocbor_enter_map(&recipient, &map) < 0) {
        return -1;
    }
    while (!nanocbor_at_end(&map)) {
        int32_t key;
        if (nanocbor_get_int32(&map, &key) < 0) {
            return -1;
        }
        if (key == COSE_KEY_PARAM_EPHEMERAL) {
            if (nanocbor_enter_map(&map, &key_map) < 0 ||
                _map_get_bstr(&key_map, COSE_KEY_PARAM_X,
                              &msg->ephemeral_pub, &eph_len) < 0 ||
                eph_len != X25519_KEYSIZE) {
                return -1;
            }
            return 0;
        }
        if (nanocbor_skip(&map) < 0) {
            return -1;
        }
    }
    return -1;
}

/* HKDF info: COSE_KDF_Context (RFC 9053 5.2), byte-identical to the host:
 * [24, [null,null,null], [null,null,null],
 *  [256, <recipient protected header bstr>]] */
static int _build_kdf_context(const cose_encrypt_msg_t *msg,
                              uint8_t *out, size_t out_len, size_t *written)
{
    nanocbor_encoder_t enc;
    nanocbor_encoder_init(&enc, out, out_len);
    nanocbor_fmt_array(&enc, 4);
    nanocbor_fmt_int(&enc, COSE_ALG_CHACHA20_POLY1305);
    for (int party = 0; party < 2; party++) {
        nanocbor_fmt_array(&enc, 3);
        for (int i = 0; i < 3; i++) {
            nanocbor_fmt_null(&enc);
        }
    }
    nanocbor_fmt_array(&enc, 2);
    nanocbor_fmt_uint(&enc, 256);
    nanocbor_put_bstr(&enc, msg->recipient_protected,
                      msg->recipient_protected_len);
    *written = nanocbor_encoded_len(&enc);
    return *written <= out_len ? 0 : -1;
}

/* AAD: Enc_structure (RFC 9052 5.3) ["Encrypt", <body protected bstr>, h''],
 * reusing the received protected-header bytes verbatim */
static int _build_enc_structure(const cose_encrypt_msg_t *msg,
                                uint8_t *out, size_t out_len, size_t *written)
{
    nanocbor_encoder_t enc;
    nanocbor_encoder_init(&enc, out, out_len);
    nanocbor_fmt_array(&enc, 3);
    nanocbor_put_tstr(&enc, "Encrypt");
    nanocbor_put_bstr(&enc, msg->body_protected, msg->body_protected_len);
    nanocbor_put_bstr(&enc, (const uint8_t *)"", 0);
    *written = nanocbor_encoded_len(&enc);
    return *written <= out_len ? 0 : -1;
}

static int _derive_cek(const cose_encrypt_msg_t *msg, byte *cek)
{
    int ret;
    uint8_t scalar[X25519_KEYSIZE];
    uint8_t shared[X25519_KEYSIZE];
    uint8_t info[64];
    size_t info_len;
    uint8_t acc = 0;

    if (_build_kdf_context(msg, info, sizeof(info), &info_len) < 0) {
        return -1;
    }

    /* both the c25519 pkg and the host's `cryptography` library use RFC 7748
     * little-endian raw keys, so no byte-order conversion is needed;
     * c25519_prepare applies the RFC 7748 scalar clamping that
     * X25519PrivateKey.exchange() performs implicitly */
    memcpy(scalar, suit_enc_seckey, sizeof(scalar));
    c25519_prepare(scalar);
    c25519_smult(shared, msg->ephemeral_pub, scalar);
    memset(scalar, 0, sizeof(scalar));

    /* RFC 7748: reject the all-zero shared secret (small-order ephemeral
     * key); `cryptography` raises on this condition too */
    for (unsigned i = 0; i < sizeof(shared); i++) {
        acc |= shared[i];
    }
    if (acc == 0) {
        memset(shared, 0, sizeof(shared));
        return -1;
    }

    ret = wc_HKDF(WC_SHA256, shared, sizeof(shared), NULL, 0,
                  info, (word32)info_len,
                  cek, CHACHA20_POLY1305_AEAD_KEYSIZE);

    memset(shared, 0, sizeof(shared));
    return ret;
}

int suit_manifest_decrypt(uint8_t *buf, size_t size,
                          const uint8_t **plaintext, size_t *plaintext_len)
{
    cose_encrypt_msg_t msg;
    byte cek[CHACHA20_POLY1305_AEAD_KEYSIZE];
    uint8_t aad[64];
    size_t aad_len;
    int ret;

    /* CBOR tag 96 = COSE_Encrypt; anything else is a plaintext manifest */
    if (size < 2 || buf[0] != 0xd8 || buf[1] != COSE_TAG_ENCRYPT) {
        *plaintext = buf;
        *plaintext_len = size;
        return SUIT_MANIFEST_ENCRYPT_PASSTHROUGH;
    }

    if (_parse_cose_encrypt(buf, size, &msg) < 0) {
        LOG_INFO("suit: COSE_Encrypt parsing failed\n");
        return -1;
    }

    ret = _derive_cek(&msg, cek);
    if (ret != 0) {
        LOG_INFO("suit: manifest CEK derivation failed: %d\n", ret);
        return ret;
    }

    if (_build_enc_structure(&msg, aad, sizeof(aad), &aad_len) < 0) {
        memset(cek, 0, sizeof(cek));
        return -1;
    }

    /* in-place: ChaCha20 is a stream cipher, the tag is checked over the
     * ciphertext before/while it is overwritten with plaintext */
    ret = wc_ChaCha20Poly1305_Decrypt(cek, msg.nonce, aad, (word32)aad_len,
                                      msg.ciphertext,
                                      (word32)msg.ciphertext_len,
                                      msg.auth_tag,
                                      (byte *)msg.ciphertext);
    memset(cek, 0, sizeof(cek));
    if (ret != 0) {
        LOG_INFO("suit: manifest decryption failed: %d\n", ret);
        return ret;
    }

    *plaintext = msg.ciphertext;
    *plaintext_len = msg.ciphertext_len;
    return 0;
}
