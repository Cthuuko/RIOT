/* 
 * Minimal example: generate an ML-DSA-65 (FIPS 204 / Dilithium Level 3)
 * keypair, sign a message, and verify the signature using wolfCrypt.
 *
 * Build:
 *   gcc wolfcrypt_mldsa65_sample.c -o wolfcrypt_mldsa65_sample -lwolfssl
 *
 * Run:
 *   ./wolfcrypt_mldsa65_sample
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef WOLFSSL_USER_SETTINGS
    #include <wolfssl/options.h>
#endif

#include <wolfssl/wolfcrypt/settings.h>
#include <wolfssl/wolfcrypt/random.h>
#include <wolfssl/wolfcrypt/dilithium.h>
#include <wolfssl/wolfcrypt/error-crypt.h>

/* ML-DSA-65 corresponds to NIST security category 3 in wolfCrypt's API */
#define ML_DSA_SECURITY_CATEGORY 3

#include </home/kuuko/masterthesis/RIOT/dist/tools/suit/ml-dsa-example/signature.h>
#include </home/kuuko/masterthesis/RIOT/dist/tools/suit/ml-dsa-example/pubkey.h>

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

int main(void)
{
    int ret;
    WC_RNG rng;
    MlDsaKey key;

    byte  *priv = NULL, *pub = NULL, *sig = NULL;
    word32 privSz = 0, pubSz = 0, sigSz = 0;

    const char *message = "Post-quantum signatures with wolfCrypt ML-DSA-65";
    word32 msgLen = (word32)strlen(message);

    int verifyResult = 0;

    /* 1. Initialize RNG */
    ret = wc_InitRng(&rng);
    if (ret != 0) {
        printf("wc_InitRng failed: %d\n", ret);
        return 1;
    }

    /* 2. Initialize the ML-DSA key object */
    ret = wc_MlDsaKey_Init(&key, NULL, INVALID_DEVID);
    if (ret != 0) {
        printf("wc_MlDsaKey_Init failed: %d\n", ret);
        goto cleanup_rng;
    }

    /* 3. Select ML-DSA-65 (NIST security category 3) */
    ret = wc_MlDsaKey_SetParams(&key, ML_DSA_SECURITY_CATEGORY);
    if (ret != 0) {
        printf("wc_MlDsaKey_SetParams failed: %d\n", ret);
        goto cleanup_key;
    }

    /* 4. Generate the keypair */
    ret = wc_MlDsaKey_MakeKey(&key, &rng);
    if (ret != 0) {
        printf("wc_MlDsaKey_MakeKey failed: %d\n", ret);
        goto cleanup_key;
    }

    /* 5. Determine key/signature sizes for ML-DSA-65 and allocate buffers */
    ret = wc_MlDsaKey_GetPrivLen(&key, (int *)&privSz);
    if (ret != 0) { printf("GetPrivLen failed: %d\n", ret); goto cleanup_key; }

    ret = wc_MlDsaKey_GetPubLen(&key, (int *)&pubSz);
    if (ret != 0) { printf("GetPubLen failed: %d\n", ret); goto cleanup_key; }

    ret = wc_MlDsaKey_GetSigLen(&key, (int *)&sigSz);
    if (ret != 0) { printf("GetSigLen failed: %d\n", ret); goto cleanup_key; }

    priv = (byte *)malloc(privSz);
    pub  = (byte *)malloc(pubSz);
    sig  = (byte *)malloc(sigSz);
    if (!priv || !pub || !sig) {
        printf("malloc failed\n");
        ret = -1;
        goto cleanup_buffers;
    }

    /* 6. Export the raw public/private key material (optional, for storage) */
    ret = wc_MlDsaKey_ExportPrivRaw(&key, priv, &privSz);
    if (ret != 0) { printf("ExportPrivRaw failed: %d\n", ret); goto cleanup_buffers; }

    ret = wc_MlDsaKey_ExportPubRaw(&key, pub, &pubSz);
    if (ret != 0) { printf("ExportPubRaw failed: %d\n", ret); goto cleanup_buffers; }

    print_hex("Private key", priv, privSz);
    print_hex("Public key",  pub,  pubSz);

    /* 7. Sign the message */
    ret = wc_MlDsaKey_Sign(&key, sig, &sigSz,
                            (const byte *)message, msgLen, &rng);
    if (ret != 0) {
        printf("wc_MlDsaKey_Sign failed: %d\n", ret);
        goto cleanup_buffers;
    }
    print_hex("Signature", sig, sigSz);

    /* 8. Verify the signature against the original message */
    ret = wc_MlDsaKey_Verify(&key, sig, sigSz,
                              (const byte *)message, msgLen, &verifyResult);
    if (ret != 0) {
        printf("wc_MlDsaKey_Verify failed: %d\n", ret);
        goto cleanup_buffers;
    }

    printf("\nMessage: \"%s\"\n", message);
    printf("Signature verification: %s\n",
           verifyResult == 1 ? "VALID" : "INVALID");


    MlDsaKey key_custom;

    /* 2. Initialize the ML-DSA key object */
    ret = wc_MlDsaKey_Init(&key_custom, NULL, INVALID_DEVID);
    if (ret != 0) {
        printf("wc_MlDsaKey_Init failed: %d\n", ret);
        goto cleanup_rng;
    }

    /* 3. Select ML-DSA-65 (NIST security category 3) */
    ret = wc_MlDsaKey_SetParams(&key_custom, ML_DSA_SECURITY_CATEGORY);
    if (ret != 0) {
        printf("wc_MlDsaKey_SetParams failed: %d\n", ret);
        goto cleanup_key;
    }

    /* 9. Import */
    ret = wc_MlDsaKey_ImportPubRaw(&key_custom, public_key, sizeof(public_key));
    if (ret != 0) {
        printf("wc_MlDsaKey_ImportPubRaw failed: %d\n", ret);
        goto cleanup_key;
    }

    ret = wc_MlDsaKey_GetPrivLen(&key_custom, (int *)&privSz);
    if (ret != 0) { printf("GetPrivLen failed: %d\n", ret); goto cleanup_key; }

    ret = wc_MlDsaKey_GetPubLen(&key_custom, (int *)&pubSz);
    if (ret != 0) { printf("GetPubLen failed: %d\n", ret); goto cleanup_key; }

    priv = (byte *)malloc(privSz);
    pub  = (byte *)malloc(pubSz);

    print_hex("Public imported key",  public_key,  sizeof(public_key));
    print_hex("Imported signautre",  signature,  sizeof(signature));


    const byte message_to_verify[] = "HelloQuantumWorld";

    msgLen = (word32)strlen(message_to_verify);

    /* 8. Verify the signature against the original message */
    ret = wc_MlDsaKey_VerifyCtx(&key_custom, signature, sizeof(signature), NULL, 0, message_to_verify, msgLen, &verifyResult);
    if (ret != 0) {
        printf("wc_MlDsaKey_VerifyCtx failed: %d\n", ret);
        goto cleanup_buffers;
    }

    printf("\nMessage: \"%s\"\n", message_to_verify);
    printf("Signature verification: %s\n",
           verifyResult == 1 ? "VALID" : "INVALID");

cleanup_buffers:
    free(priv);
    free(pub);
    free(sig);
cleanup_key:
    wc_MlDsaKey_Free(&key);
cleanup_rng:
    wc_FreeRng(&rng);

    return ret;
}
