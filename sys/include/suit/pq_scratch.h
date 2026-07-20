/*
 * Copyright (C) 2026 Miguel Arcilla
 *
 * This file is subject to the terms and conditions of the GNU Lesser
 * General Public License v2.1. See the file LICENSE in the top level
 * directory for more details.
 */

/**
 * @ingroup     sys_suit
 * @brief       Shared static workspace for the post-quantum SUIT crypto
 *
 * When a firmware carries both the ML-DSA signature verifier (multi-KB
 * static `MlDsaKey` verify state, see pkg/libcose's wolfcrypt_mldsa
 * backend) and ML-KEM manifest decryption (multi-KB static `MlKemKey`,
 * see sys/suit/encrypt/decrypt.c), the two never run concurrently:
 * `suit_handle_manifest_buf()` finishes decryption — CEK derived, key
 * state freed — before `suit_parse()` starts signature verification, all
 * on the single SUIT worker thread. Overlaying them in one union recovers
 * `sizeof(MlKemKey)` (~3.4 KB) of .bss — the difference between fitting
 * and not fitting ML-DSA-44 + ML-KEM-768 in samr21-xpro's 32 KB RAM.
 *
 * The union is only declared when BOTH wolfcrypt modules are compiled in;
 * single-scheme builds keep their own private static objects. The object
 * itself is defined in sys/suit/encrypt/decrypt.c (always compiled when
 * ML-KEM manifest decryption is).
 *
 * @{
 *
 * @file
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 */
#ifndef SUIT_PQ_SCRATCH_H
#define SUIT_PQ_SCRATCH_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Size of the streaming firmware-payload decryptor's COSE header
 *          buffer (sys/suit/encrypt/payload_decrypt.c): the ML-KEM
 *          encapsulation ciphertext dominates in KEM builds; X25519
 *          headers are 74 B measured
 */
#if defined(MODULE_WOLFCRYPT_MLKEM1024)
#define SUIT_FW_ENC_HDR_LEN     (1696)
#elif defined(MODULE_WOLFCRYPT_MLKEM)
#define SUIT_FW_ENC_HDR_LEN     (1216)
#else
#define SUIT_FW_ENC_HDR_LEN     (192)
#endif

#if defined(MODULE_WOLFCRYPT_MLDSA) && defined(MODULE_WOLFCRYPT_MLKEM)

#define SUIT_PQ_SCRATCH_SHARED 1

#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/dilithium.h>
#include <wolfssl/wolfcrypt/wc_mlkem.h>

/**
 * @brief   One static allocation shared by the (never concurrent) ML-DSA
 *          verify state and the encryption-side states
 *
 * The encryption members may be live at the same time as each other (the
 * payload header buffer holds the KEM ciphertext *while* it is being
 * decapsulated), so they are struct siblings — but all of them are dead
 * whenever the ML-DSA verify state is live (verification finishes before
 * the fetch directive runs), hence the outer union.
 */
union suit_pq_scratch {
    MlDsaKey mldsa;         /**< signature-verify state (libcose backend) */
    struct {
        MlKemKey mlkem;     /**< decapsulation state (manifest + payload
                                 CEK derivation, suit encrypt module) */
#ifdef MODULE_SUIT_FIRMWARE_ENCRYPT
        uint8_t fw_hdr[SUIT_FW_ENC_HDR_LEN];
                            /**< streaming payload-decrypt header buffer */
#endif
    } enc;                  /**< encryption-side states (concurrent) */
};

extern union suit_pq_scratch suit_pq_scratch;

#endif /* MODULE_WOLFCRYPT_MLDSA && MODULE_WOLFCRYPT_MLKEM */

#ifdef __cplusplus
}
#endif

#endif /* SUIT_PQ_SCRATCH_H */
/** @} */
