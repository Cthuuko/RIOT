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
 * @brief       Internals shared between SUIT manifest decryption
 *              (decrypt.c) and streaming firmware-payload decryption
 *              (payload_decrypt.c): COSE_Encrypt parsing, CEK derivation,
 *              and Enc_structure AAD building. Private to sys/suit/encrypt.
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 * @}
 */
#ifndef SUIT_ENCRYPT_INTERNAL_H
#define SUIT_ENCRYPT_INTERNAL_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Parsed fields of a COSE_Encrypt container (pointers into the
 *          parsed buffer)
 *
 * The ciphertext may be attached (bstr slot: @p ciphertext / @p auth_tag
 * set — the manifest container) or detached (null slot: @p ciphertext is
 * NULL and the ciphertext streams after the header — the firmware-payload
 * container).
 */
typedef struct {
    const uint8_t *body_protected;      /**< serialized {1: 24} */
    size_t body_protected_len;          /**< length of body_protected */
    const uint8_t *recipient_protected; /**< serialized {1: <recipient alg>} */
    size_t recipient_protected_len;     /**< length of recipient_protected */
    const uint8_t *nonce;               /**< 12 bytes */
    const uint8_t *ciphertext;          /**< without trailing tag; NULL if
                                             detached */
    size_t ciphertext_len;              /**< 0 if detached */
    const uint8_t *auth_tag;            /**< 16 bytes; NULL if detached */
#ifdef MODULE_WOLFCRYPT_MLKEM
    const uint8_t *kem_ct;              /**< ML-KEM encapsulation ciphertext */
    size_t kem_ct_len;                  /**< length of kem_ct */
#else
    const uint8_t *ephemeral_pub;       /**< 32-byte ephemeral X25519 key */
#endif
} cose_encrypt_msg_t;

/**
 * @brief   Parse a COSE_Encrypt container (attached or detached ciphertext)
 *
 * Fully unwinds every nested CBOR container, which both validates that
 * @p buf holds the complete header (truncated input fails — callers
 * accumulating a streamed header simply retry with more data) and yields
 * the exact encoded length.
 *
 * @return  the container's encoded length in bytes (for a detached
 *          container, the ciphertext stream starts at this offset)
 * @return  <0 on parse error, truncation, or recipient-algorithm mismatch
 */
ssize_t suit_cose_encrypt_parse(const uint8_t *buf, size_t len,
                                cose_encrypt_msg_t *msg);

/**
 * @brief   Derive the ChaCha20-Poly1305 content-encryption key
 *
 * X25519 ECDH (or ML-KEM decapsulation) with the embedded device key,
 * then HKDF-SHA256 with the byte-exact COSE_KDF_Context as info.
 *
 * @param[in]   msg     parsed container
 * @param[out]  cek     32-byte content-encryption key
 *
 * @return  0 on success, <0/wolfCrypt error otherwise
 */
int suit_cose_derive_cek(const cose_encrypt_msg_t *msg, uint8_t *cek);

/**
 * @brief   Build the Enc_structure AAD (RFC 9052 §5.3), reusing the
 *          received protected-header bytes verbatim
 *
 * @return  0 on success (@p written set), <0 if @p out is too small
 */
int suit_cose_build_enc_structure(const cose_encrypt_msg_t *msg,
                                  uint8_t *out, size_t out_len,
                                  size_t *written);

#ifdef __cplusplus
}
#endif

#endif /* SUIT_ENCRYPT_INTERNAL_H */
/** @} */
