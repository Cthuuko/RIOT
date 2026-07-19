/*
 * Device side of the ML-KEM manifest-encryption example: decrypt a
 * COSE_Encrypt container produced by encrypt_manifest.py (Python
 * `cryptography`) using wolfCrypt, proving cross-implementation interop of
 * the full pipeline: ML-KEM decapsulation -> HKDF-SHA256 ->
 * ChaCha20-Poly1305. Post-quantum variant of
 * ../manifest-encryption/wolfcrypt-sample.c (which uses X25519 ECDH).
 *
 * The device key is embedded as the 64-byte FIPS 203 seed (d||z);
 * wc_MlKemKey_MakeKeyWithRandom() re-expands it deterministically, and the
 * derived encapsulation key is compared against the Python-exported one
 * (device_pubkey.h) to prove the seed interop before decapsulating.
 *
 * Build (against the pre-built wolfSSL checkout of the ml-dsa example —
 * ML-KEM is already compiled in, no reconfigure needed — plus RIOT's
 * nanocbor checkout); MLKEM_LEVEL selects 768 (default) or 1024:
 *   python3 encrypt_manifest.py --level 768
 *   gcc wolfcrypt-sample.c ../../../../build/pkg/nanocbor/src/decoder.c \
 *       ../../../../build/pkg/nanocbor/src/encoder.c \
 *       -DMLKEM_LEVEL=768 -o mlkem768-example \
 *       -I../../../../build/pkg/nanocbor/include \
 *       -I../../../../dist/tools/suit/ml-dsa-example/wolfssl \
 *       -L../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs \
 *       -Wl,-rpath,"$(realpath ../../../../dist/tools/suit/ml-dsa-example/wolfssl/src/.libs)" \
 *       -lwolfssl
 *
 * Run:
 *   ./mlkem768-example
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef WOLFSSL_USER_SETTINGS
    #include <wolfssl/options.h>
#endif

#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/wc_mlkem.h>
#include <wolfssl/wolfcrypt/hmac.h>
#include <wolfssl/wolfcrypt/kdf.h>
#include <wolfssl/wolfcrypt/chacha20_poly1305.h>
#include <wolfssl/wolfcrypt/error-crypt.h>

#include <nanocbor/nanocbor.h>

#include "encrypted.h"      /* const byte cose_encrypt[] */
#include "device_seckey.h"  /* const byte device_seckey[64] (d||z seed) */
#include "device_pubkey.h"  /* const byte device_pubkey[1184|1568] */
#include "plaintext.h"      /* const byte expected_plaintext[] */

#ifndef MLKEM_LEVEL
#define MLKEM_LEVEL 768
#endif

/* Private-use COSE alg IDs — must match encrypt_manifest.py */
#if MLKEM_LEVEL == 768
    #define MLKEM_TYPE          WC_ML_KEM_768
    #define COSE_ALG_MLKEM     (-70768)
    #define MLKEM_CT_SIZE       WC_ML_KEM_768_CIPHER_TEXT_SIZE
    #define MLKEM_PUB_SIZE      WC_ML_KEM_768_PUBLIC_KEY_SIZE
#elif MLKEM_LEVEL == 1024
    #define MLKEM_TYPE          WC_ML_KEM_1024
    #define COSE_ALG_MLKEM     (-70769)
    #define MLKEM_CT_SIZE       WC_ML_KEM_1024_CIPHER_TEXT_SIZE
    #define MLKEM_PUB_SIZE      WC_ML_KEM_1024_PUBLIC_KEY_SIZE
#else
    #error "MLKEM_LEVEL must be 768 or 1024"
#endif

#define COSE_ALG_CHACHA20_POLY1305  24
#define COSE_TAG_ENCRYPT            96
#define COSE_HDR_IV                 5

#define NONCE_LEN  CHACHA20_POLY1305_AEAD_IV_SIZE      /* 12 */
#define TAG_LEN    CHACHA20_POLY1305_AEAD_AUTHTAG_SIZE /* 16 */

/* Parsed fields of the COSE_Encrypt container (pointers into cose_encrypt) */
typedef struct {
    const uint8_t *body_protected;      /* serialized {1: 24} */
    size_t body_protected_len;
    const uint8_t *recipient_protected; /* serialized {1: -70768|-70769} */
    size_t recipient_protected_len;
    const uint8_t *nonce;               /* 12 bytes */
    const uint8_t *ciphertext;          /* without the trailing tag */
    size_t ciphertext_len;
    const uint8_t *auth_tag;            /* 16 bytes */
    const uint8_t *kem_ct;              /* ML-KEM encapsulation ciphertext */
    size_t kem_ct_len;
} cose_encrypt_msg_t;

static void print_hex(const char *label, const byte *data, word32 len)
{
    word32 i;
    printf("%s (%u bytes): ", label, (unsigned)len);
    for (i = 0; i < len && i < 32; i++) {       /* print first 32 bytes only */
        printf("%02x", data[i]);
    }
    if (len > 32) printf("...");
    printf("\n");
}

/* Fetch the value for `wanted` from a CBOR map, skipping other entries */
static int map_get_bstr(nanocbor_value_t *map, int32_t wanted,
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

static int parse_cose_encrypt(const uint8_t *buf, size_t len,
                              cose_encrypt_msg_t *msg)
{
    nanocbor_value_t it, body, map, recipients, recipient;
    uint32_t tag;
    const uint8_t *ct;
    size_t ct_len, nonce_len;

    nanocbor_decoder_init(&it, buf, len);
    if (nanocbor_get_tag(&it, &tag) < 0 || tag != COSE_TAG_ENCRYPT) {
        printf("not a COSE_Encrypt (tag 96) container\n");
        return -1;
    }
    if (nanocbor_enter_array(&it, &body) < 0 ||
        nanocbor_get_bstr(&body, &msg->body_protected,
                          &msg->body_protected_len) < 0) {
        return -1;
    }
    if (nanocbor_enter_map(&body, &map) < 0 ||
        map_get_bstr(&map, COSE_HDR_IV, &msg->nonce, &nonce_len) < 0 ||
        nonce_len != NONCE_LEN) {
        printf("missing/invalid IV in unprotected header\n");
        return -1;
    }
    nanocbor_leave_container(&body, &map);
    if (nanocbor_get_bstr(&body, &ct, &ct_len) < 0 || ct_len < TAG_LEN) {
        return -1;
    }
    msg->ciphertext = ct;
    msg->ciphertext_len = ct_len - TAG_LEN;
    msg->auth_tag = ct + ct_len - TAG_LEN;

    /* single recipient: [protected, {}, <KEM ciphertext>] */
    if (nanocbor_enter_array(&body, &recipients) < 0 ||
        nanocbor_enter_array(&recipients, &recipient) < 0 ||
        nanocbor_get_bstr(&recipient, &msg->recipient_protected,
                          &msg->recipient_protected_len) < 0) {
        return -1;
    }
    if (nanocbor_skip(&recipient) < 0) {    /* unprotected map (empty) */
        return -1;
    }
    if (nanocbor_get_bstr(&recipient, &msg->kem_ct, &msg->kem_ct_len) < 0 ||
        msg->kem_ct_len != MLKEM_CT_SIZE) {
        printf("missing/invalid KEM ciphertext\n");
        return -1;
    }
    return 0;
}

/* HKDF info: COSE_KDF_Context (RFC 9053 5.2) — must match encrypt_manifest.py
 * byte-for-byte: [24, [null,null,null], [null,null,null],
 *                 [256, <recipient protected header bstr>]] */
static int build_kdf_context(const cose_encrypt_msg_t *msg,
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
static int build_enc_structure(const cose_encrypt_msg_t *msg,
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

/* Expand the seed, check pubkey interop, decapsulate `kem_ct`, HKDF -> CEK */
static int derive_cek(const cose_encrypt_msg_t *msg,
                      const uint8_t *kem_ct, byte *cek)
{
    int ret;
    static MlKemKey key;    /* multi-KB struct: keep off the stack */
    byte pub[MLKEM_PUB_SIZE];
    byte shared[WC_ML_KEM_SS_SZ];
    uint8_t info[64];
    size_t info_len;

    if (build_kdf_context(msg, info, sizeof(info), &info_len) < 0) {
        printf("KDF context encoding failed\n");
        return -1;
    }

    ret = wc_MlKemKey_Init(&key, MLKEM_TYPE, NULL, INVALID_DEVID);
    if (ret != 0) {
        printf("wc_MlKemKey_Init failed: %d\n", ret);
        return ret;
    }

    /* deterministic expansion of the 64-byte d||z seed — the same expansion
     * cryptography's from_seed_bytes()/generate() performs */
    ret = wc_MlKemKey_MakeKeyWithRandom(&key, device_seckey,
                                        sizeof(device_seckey));
    if (ret == 0) {
        /* interop proof: the seed-derived encapsulation key must equal the
         * one Python exported */
        ret = wc_MlKemKey_EncodePublicKey(&key, pub, sizeof(pub));
        if (ret == 0 &&
            (sizeof(pub) != sizeof(device_pubkey) ||
             memcmp(pub, device_pubkey, sizeof(pub)) != 0)) {
            printf("seed-derived public key MISMATCH vs device_pubkey.h\n");
            ret = -1;
        }
        else if (ret == 0) {
            printf("Seed-derived public key matches Python's: OK\n");
        }
    }
    if (ret == 0) {
        ret = wc_MlKemKey_Decapsulate(&key, shared, kem_ct,
                                      (word32)msg->kem_ct_len);
    }
    if (ret == 0) {
        print_hex("Shared secret", shared, sizeof(shared));
        ret = wc_HKDF(WC_SHA256, shared, sizeof(shared), NULL, 0,
                      info, (word32)info_len,
                      cek, CHACHA20_POLY1305_AEAD_KEYSIZE);
    }

    memset(shared, 0, sizeof(shared));
    wc_MlKemKey_Free(&key);
    return ret;
}

static int aead_decrypt(const cose_encrypt_msg_t *msg, const byte *cek,
                        const uint8_t *aad, size_t aad_len,
                        const uint8_t *ciphertext, byte *plain)
{
    return wc_ChaCha20Poly1305_Decrypt(cek, msg->nonce, aad, (word32)aad_len,
                                       ciphertext,
                                       (word32)msg->ciphertext_len,
                                       msg->auth_tag, plain);
}

int main(void)
{
    int ret;
    cose_encrypt_msg_t msg;
    byte cek[CHACHA20_POLY1305_AEAD_KEYSIZE];
    byte bad_cek[CHACHA20_POLY1305_AEAD_KEYSIZE];
    uint8_t aad[64];
    size_t aad_len;
    byte *plain;

    printf("COSE_Encrypt (ML-KEM-%d) manifest decryption with wolfCrypt "
           "- START\n", MLKEM_LEVEL);

    if (parse_cose_encrypt(cose_encrypt, sizeof(cose_encrypt), &msg) < 0) {
        printf("COSE_Encrypt parsing failed\n");
        return 1;
    }
    print_hex("KEM ciphertext", msg.kem_ct, (word32)msg.kem_ct_len);
    print_hex("Nonce", msg.nonce, NONCE_LEN);
    print_hex("Ciphertext", msg.ciphertext, (word32)msg.ciphertext_len);

    ret = derive_cek(&msg, msg.kem_ct, cek);
    if (ret != 0) {
        printf("CEK derivation failed: %d\n", ret);
        return 1;
    }

    if (build_enc_structure(&msg, aad, sizeof(aad), &aad_len) < 0) {
        printf("Enc_structure encoding failed\n");
        return 1;
    }

    plain = (byte *)malloc(msg.ciphertext_len);
    if (plain == NULL) {
        printf("malloc failed\n");
        return 1;
    }

    ret = aead_decrypt(&msg, cek, aad, aad_len, msg.ciphertext, plain);
    if (ret != 0) {
        printf("wc_ChaCha20Poly1305_Decrypt failed: %d\n", ret);
        free(plain);
        return 1;
    }

    printf("\nRecovered plaintext (%u bytes): %.*s\n",
           (unsigned)msg.ciphertext_len,
           (int)msg.ciphertext_len, (const char *)plain);
    if (msg.ciphertext_len == sizeof(expected_plaintext) &&
        memcmp(plain, expected_plaintext, sizeof(expected_plaintext)) == 0) {
        printf("Python-encrypted, wolfCrypt-decrypted: MATCH\n");
    }
    else {
        printf("Python-encrypted, wolfCrypt-decrypted: MISMATCH\n");
        free(plain);
        return 1;
    }

    /* Negative test 1: a flipped AEAD-ciphertext byte must fail the tag */
    {
        byte *tampered = (byte *)malloc(msg.ciphertext_len);
        if (tampered == NULL) {
            printf("malloc failed\n");
            free(plain);
            return 1;
        }
        memcpy(tampered, msg.ciphertext, msg.ciphertext_len);
        tampered[0] ^= 0x01;
        ret = aead_decrypt(&msg, cek, aad, aad_len, tampered, plain);
        printf("Tampered AEAD ciphertext rejected: %s (ret %d)\n",
               ret != 0 ? "YES" : "NO - BUG", ret);
        free(tampered);
        if (ret == 0) {
            free(plain);
            return 1;
        }
    }

    /* Negative test 2: a flipped KEM-ciphertext byte triggers FIPS 203
     * implicit rejection — decapsulation "succeeds" but yields a different
     * shared secret, so the derived CEK fails the Poly1305 tag */
    {
        byte *bad_kem = (byte *)malloc(msg.kem_ct_len);
        if (bad_kem == NULL) {
            printf("malloc failed\n");
            free(plain);
            return 1;
        }
        memcpy(bad_kem, msg.kem_ct, msg.kem_ct_len);
        bad_kem[0] ^= 0x01;
        ret = derive_cek(&msg, bad_kem, bad_cek);
        if (ret != 0) {
            /* an explicit decapsulation error is an acceptable rejection
             * path too, just not the implicit-rejection one */
            printf("Tampered KEM ciphertext rejected: YES "
                   "(decapsulation error %d)\n", ret);
        }
        else {
            ret = aead_decrypt(&msg, bad_cek, aad, aad_len,
                               msg.ciphertext, plain);
            printf("Tampered KEM ciphertext rejected: %s "
                   "(implicit rejection -> tag error %d)\n",
                   ret != 0 ? "YES" : "NO - BUG", ret);
        }
        free(bad_kem);
        if (ret == 0) {
            free(plain);
            return 1;
        }
    }

    free(plain);
    printf("COSE_Encrypt (ML-KEM-%d) manifest decryption with wolfCrypt "
           "- END\n", MLKEM_LEVEL);
    return 0;
}
