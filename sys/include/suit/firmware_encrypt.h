/*
 * Copyright (C) 2026 Miguel Arcilla
 *
 * This file is subject to the terms and conditions of the GNU Lesser
 * General Public License v2.1. See the file LICENSE in the top level
 * directory for more details.
 */

/**
 * @ingroup     sys_suit
 * @brief       SUIT firmware-payload confidentiality (streaming
 *              COSE_Encrypt decryption)
 *
 * Decrypts firmware payloads wrapped in a detached-ciphertext COSE_Encrypt
 * container (header || ciphertext || Poly1305 tag — see
 * examples/advanced/suit_update/firmware-encryption/README.md for the wire
 * format) while they stream in from the transport: a wrapper callback sits
 * in front of the storage-write helper in the SUIT fetch directive,
 * reassembles and parses the container header, derives the
 * ChaCha20-Poly1305 content key with the same embedded device key and
 * recipient scheme as manifest encryption (X25519 ECDH-ES or ML-KEM), and
 * forwards decrypted chunks with plaintext offsets. The trailing tag is
 * verified before the final chunk is forwarded, so a tampered stream is
 * never finalized; the signed manifest's image digest over the stored
 * plaintext remains the authenticity anchor.
 *
 * Payloads that do not start with a COSE_Encrypt tag pass through
 * unchanged (legacy plaintext payloads keep working).
 *
 * @{
 *
 * @file
 * @brief       SUIT streaming firmware-payload decryption API
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 */
#ifndef SUIT_FIRMWARE_ENCRYPT_H
#define SUIT_FIRMWARE_ENCRYPT_H

#include <stddef.h>
#include <stdint.h>

#include "net/nanocoap.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Arm the streaming payload decryptor for one fetch
 *
 * Resets the internal state machine and records the storage callback that
 * receives the decrypted (or passed-through) chunks. Must be called
 * immediately before starting a transport transfer that uses
 * @ref suit_payload_decrypt_helper as its blockwise callback. The SUIT
 * worker is single-threaded, so one static state instance suffices.
 *
 * @param[in]   inner   the storage-write callback to forward plaintext to
 */
void suit_payload_decrypt_start(coap_blockwise_cb_t inner);

/**
 * @brief   Transport blockwise callback wrapping a storage helper with
 *          streaming COSE_Encrypt decryption
 *
 * Drop-in replacement for the storage helper in the fetch directive's
 * transport calls; chunks must arrive in order (both the CoAP blockwise
 * and VFS transports guarantee this). Decrypts in place inside @p buf.
 *
 * @return  0 on success, <0 on parse/crypto/authentication/storage error
 *          (aborts the transfer)
 */
int suit_payload_decrypt_helper(void *arg, size_t offset, uint8_t *buf,
                                size_t len, int more);

#ifdef __cplusplus
}
#endif

#endif /* SUIT_FIRMWARE_ENCRYPT_H */
/** @} */
