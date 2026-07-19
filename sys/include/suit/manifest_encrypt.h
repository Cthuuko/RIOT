/*
 * Copyright (C) 2026 Miguel Arcilla
 *
 * This file is subject to the terms and conditions of the GNU Lesser
 * General Public License v2.1. See the file LICENSE in the top level
 * directory for more details.
 */

/**
 * @ingroup     sys_suit
 * @brief       SUIT manifest confidentiality (COSE_Encrypt decryption)
 *
 * Decrypts RFC 9770-style COSE_Encrypt containers wrapping a signed SUIT
 * manifest: ephemeral-static X25519 ECDH (COSE ECDH-ES + HKDF-256) derives a
 * ChaCha20-Poly1305 content key via wolfCrypt. The device's static X25519
 * private key is embedded at build time (suit_enc_seckey.h, generated from
 * SUIT_ENC_SEC by makefiles/suit.base.inc.mk).
 *
 * @{
 *
 * @file
 * @brief       SUIT manifest decryption API
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 */
#ifndef SUIT_MANIFEST_ENCRYPT_H
#define SUIT_MANIFEST_ENCRYPT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Manifest was not encrypted (no COSE_Encrypt tag) and is passed
 *          through unchanged
 */
#define SUIT_MANIFEST_ENCRYPT_PASSTHROUGH   (1)

/**
 * @brief   Decrypt a COSE_Encrypt-wrapped SUIT manifest in place
 *
 * If @p buf starts with a COSE_Encrypt container (CBOR tag 96), it is
 * decrypted in place (ChaCha20 is a stream cipher, so the plaintext
 * overwrites the ciphertext bytes within @p buf) and @p plaintext /
 * @p plaintext_len are pointed at the recovered signed manifest inside
 * @p buf. Anything else is treated as a plaintext manifest and passed
 * through unchanged, so an encryption-capable firmware still accepts
 * legacy unencrypted manifests.
 *
 * @param[in,out]   buf             manifest buffer (modified on decryption)
 * @param[in]       size            length of the (possibly encrypted) manifest
 * @param[out]      plaintext       points into @p buf at the plaintext manifest
 * @param[out]      plaintext_len   length of the plaintext manifest
 *
 * @return  0 on successful decryption
 * @return  @ref SUIT_MANIFEST_ENCRYPT_PASSTHROUGH if @p buf is not encrypted
 * @return  <0 on parse, key-agreement, or authentication failure
 */
int suit_manifest_decrypt(uint8_t *buf, size_t size,
                          const uint8_t **plaintext, size_t *plaintext_len);

#ifdef __cplusplus
}
#endif

#endif /* SUIT_MANIFEST_ENCRYPT_H */
/** @} */
