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
 * @brief       Streaming SUIT firmware-payload decryption
 *              (detached-ciphertext COSE_Encrypt, ChaCha20-Poly1305 via
 *              wolfCrypt's incremental AEAD API)
 *
 * Device-side counterpart of
 * examples/advanced/suit_update/firmware-encryption/encrypt_firmware.py,
 * ported from that example's wolfcrypt-sample.c streaming state machine
 * (header reassembly across transport chunks, in-place chunk decryption,
 * 16-byte trailing-tag lag buffer). Container parsing and CEK derivation
 * are shared with the manifest decryption in decrypt.c (encrypt_internal.h).
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 * @}
 */

#ifdef MODULE_SUIT_FIRMWARE_ENCRYPT

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "log.h"
#include "suit/firmware_encrypt.h"
#include "suit/perf.h"

#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/chacha20_poly1305.h>

#include "encrypt_internal.h"
#include "suit/pq_scratch.h"    /* SUIT_FW_ENC_HDR_LEN (+ shared buffer) */

#define COSE_TAG_ENCRYPT    96
#define TAG_LEN             CHACHA20_POLY1305_AEAD_AUTHTAG_SIZE /* 16 */

/* Header buffer: must hold the complete detached-ciphertext COSE_Encrypt
 * header — X25519: 74 B measured + framing headroom; ML-KEM: the KEM
 * encapsulation ciphertext dominates (1088/1568 B). Sizes defined next to
 * the union in suit/pq_scratch.h: in ML-DSA + ML-KEM builds the buffer
 * overlays the idle ML-DSA verify state (the difference between the
 * full-PQ combo fitting and overflowing samr21-xpro's 32 KB RAM). */
#ifdef SUIT_PQ_SCRATCH_SHARED
#define _hdr_buf (suit_pq_scratch.enc.fw_hdr)
#else
static uint8_t _hdr_storage[SUIT_FW_ENC_HDR_LEN];
#define _hdr_buf _hdr_storage
#endif

typedef struct {
    coap_blockwise_cb_t inner;  /* storage helper receiving plaintext */
    bool passthrough;           /* payload is not encrypted */
    bool hdr_done;
    bool failed;
    bool started;               /* first chunk seen */
    size_t hdr_fill;            /* bytes accumulated in _hdr_buf */
    ChaChaPoly_Aead aead;
    uint8_t pending[TAG_LEN];   /* tag lag: last 16 bytes seen so far */
    size_t pending_fill;
    size_t pt_off;              /* plaintext bytes forwarded so far */
} payload_decrypt_state_t;

/* Multi-hundred-byte state: static, not on the 4 KB worker stack
 * (MLDSA_HARDWARE_FIXES.md lesson). The SUIT worker is single-threaded,
 * so a single instance is safe. */
static payload_decrypt_state_t _state;

void suit_payload_decrypt_start(coap_blockwise_cb_t inner)
{
    memset(&_state, 0, sizeof(_state));
    _state.inner = inner;
}

/* Feed ciphertext into the AEAD, always withholding the most recent
 * TAG_LEN bytes (candidate trailing tag) in _state.pending; decrypts
 * in place and forwards plaintext to the storage helper */
static int _feed_ciphertext(payload_decrypt_state_t *s, void *arg,
                            uint8_t *data, size_t len)
{
    size_t total = s->pending_fill + len;

    if (total > TAG_LEN) {
        size_t n = total - TAG_LEN;             /* safely decryptable */
        size_t from_pending = n < s->pending_fill ? n : s->pending_fill;

        if (from_pending > 0) {
            uint8_t tmp[TAG_LEN];
            suit_perf_begin(SUIT_PERF_PAYLOAD_AEAD);
            int aead_res = wc_ChaCha20Poly1305_UpdateData(&s->aead, s->pending,
                                                          tmp,
                                                          (word32)from_pending);
            suit_perf_end(SUIT_PERF_PAYLOAD_AEAD);
            suit_perf_count(SUIT_PERF_PAYLOAD_AEAD, from_pending);
            if (aead_res != 0) {
                return -1;
            }
            if (s->inner(arg, s->pt_off, tmp, from_pending, 1) < 0) {
                return -1;
            }
            s->pt_off += from_pending;
            memmove(s->pending, s->pending + from_pending,
                    s->pending_fill - from_pending);
            s->pending_fill -= from_pending;
        }

        size_t from_data = n - from_pending;
        if (from_data > 0) {
            /* in place: input and output may alias for ChaCha20 */
            suit_perf_begin(SUIT_PERF_PAYLOAD_AEAD);
            int aead_res = wc_ChaCha20Poly1305_UpdateData(&s->aead, data, data,
                                                          (word32)from_data);
            suit_perf_end(SUIT_PERF_PAYLOAD_AEAD);
            suit_perf_count(SUIT_PERF_PAYLOAD_AEAD, from_data);
            if (aead_res != 0) {
                return -1;
            }
            if (s->inner(arg, s->pt_off, data, from_data, 1) < 0) {
                return -1;
            }
            s->pt_off += from_data;
            data += from_data;
            len -= from_data;
        }
    }

    /* stash the (potential) tag tail */
    memcpy(s->pending + s->pending_fill, data, len);
    s->pending_fill += len;
    return 0;
}

int suit_payload_decrypt_helper(void *arg, size_t offset, uint8_t *buf,
                                size_t len, int more)
{
    payload_decrypt_state_t *s = &_state;

    if (s->failed || s->inner == NULL) {
        return -1;
    }

    if (!s->started && len > 0) {
        s->started = true;
        if (len < 2 || buf[0] != 0xd8 || buf[1] != COSE_TAG_ENCRYPT) {
            LOG_INFO("suit: payload not encrypted, passing through\n");
            s->passthrough = true;
        }
    }
    if (s->passthrough || (!s->started && !more)) {
        /* not encrypted (or empty payload): forward unchanged */
        return s->inner(arg, offset, buf, len, more);
    }

    if (!s->hdr_done && len > 0) {
        size_t space = SUIT_FW_ENC_HDR_LEN - s->hdr_fill;
        size_t take = len < space ? len : space;
        if (take == 0) {
            LOG_INFO("suit: payload COSE header exceeds %u bytes\n",
                     (unsigned)SUIT_FW_ENC_HDR_LEN);
            goto fail;
        }
        memcpy(_hdr_buf + s->hdr_fill, buf, take);
        s->hdr_fill += take;
        buf += take;
        len -= take;

        cose_encrypt_msg_t msg;
        ssize_t hdr_len = suit_cose_encrypt_parse(_hdr_buf, s->hdr_fill, &msg);
        if (hdr_len < 0 || msg.ciphertext != NULL /* must be detached */) {
            if (hdr_len >= 0 || len > 0) {
                /* attached container, or full buffer still unparseable */
                LOG_INFO("suit: payload COSE header parsing failed\n");
                goto fail;
            }
            return 0;               /* incomplete: wait for the next chunk */
        }

        uint8_t cek[CHACHA20_POLY1305_AEAD_KEYSIZE];
        uint8_t aad[64];
        size_t aad_len;
        suit_perf_begin(SUIT_PERF_PAYLOAD_KEM);
        int ret = suit_cose_derive_cek(&msg, cek);
        suit_perf_end(SUIT_PERF_PAYLOAD_KEM);
        /* deepest point of the call chain: worker -> parse -> fetch ->
         * transport callback -> decapsulation */
        suit_perf_stack_sample(SUIT_PERF_PAYLOAD_KEM);
        if (ret != 0) {
            LOG_INFO("suit: payload CEK derivation failed: %d\n", ret);
            goto fail;
        }
        if (suit_cose_build_enc_structure(&msg, aad, sizeof(aad),
                                          &aad_len) < 0 ||
            wc_ChaCha20Poly1305_Init(&s->aead, cek, msg.nonce,
                                     CHACHA20_POLY1305_AEAD_DECRYPT) != 0 ||
            wc_ChaCha20Poly1305_UpdateAad(&s->aead, aad,
                                          (word32)aad_len) != 0) {
            memset(cek, 0, sizeof(cek));
            goto fail;
        }
        memset(cek, 0, sizeof(cek));
        s->hdr_done = true;
        LOG_INFO("suit: decrypting payload (header %d bytes)\n",
                 (int)hdr_len);

        /* bytes past the header already in the buffer are ciphertext */
        if (s->hdr_fill > (size_t)hdr_len) {
            if (_feed_ciphertext(s, arg, _hdr_buf + hdr_len,
                                 s->hdr_fill - hdr_len) < 0) {
                goto fail;
            }
        }
    }

    if (len > 0 && _feed_ciphertext(s, arg, buf, len) < 0) {
        goto fail;
    }

    if (!more) {
        uint8_t computed[TAG_LEN];
        if (!s->hdr_done || s->pending_fill != TAG_LEN) {
            LOG_INFO("suit: payload stream truncated\n");
            goto fail;
        }
        suit_perf_begin(SUIT_PERF_PAYLOAD_AEAD);
        int tag_res = wc_ChaCha20Poly1305_Final(&s->aead, computed);
        if (tag_res == 0) {
            tag_res = wc_ChaCha20Poly1305_CheckTag(computed, s->pending);
        }
        suit_perf_end(SUIT_PERF_PAYLOAD_AEAD);
        if (tag_res != 0) {
            LOG_INFO("suit: payload authentication failed\n");
            goto fail;
        }
        LOG_INFO("suit: payload decrypted (%u bytes)\n",
                 (unsigned)s->pt_off);
        /* only now does the storage helper see end-of-stream (finalize) */
        int res = s->inner(arg, s->pt_off, buf, 0, 0);
        memset(&s->aead, 0, sizeof(s->aead));
        return res;
    }
    return 0;

fail:
    s->failed = true;
    memset(&s->aead, 0, sizeof(s->aead));
    return -1;
}

#else
typedef int dont_be_pedantic;
#endif /* MODULE_SUIT_FIRMWARE_ENCRYPT */
