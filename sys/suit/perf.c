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
 * @brief       Performance and memory checkpoints for the SUIT update
 *              (`suit_perf` module, opt-in via SUIT_PERF=1)
 *
 * Collects per-phase timing, byte and call counters plus the worker stack /
 * heap high-water marks of one update run, and prints them both as a human
 * table and as grep-able CSV lines. The measurement taxonomy, the CSV schema
 * and the capture procedure are documented in
 * examples/advanced/suit_update/PERFORMANCE.md.
 *
 * The whole file self-disables when the module is not selected, mirroring
 * sys/suit/encrypt/payload_decrypt.c — sys/suit/Makefile compiles the
 * directory unconditionally.
 *
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 * @}
 */

#ifdef MODULE_SUIT_PERF

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "malloc_monitor.h"
#include "thread.h"
#include "ztimer.h"

#include "suit/perf.h"

#ifdef MODULE_SUIT_MANIFEST_ENCRYPT
#include "suit/pq_scratch.h"
#endif

/* Whole-heap figure, when the build can take it. heap_stats() reaches into
 * newlib's mallinfo(), which drags in the same object as _malloc_stats_r and
 * with it a reference to fiprintf. On boards that swap newlib's stdio for
 * mpaland-printf (RIOT's default on small newlib targets, e.g. samr21-xpro)
 * fiprintf is deliberately wrapped to an undefined symbol to catch exactly
 * that mixing, so calling heap_stats() there is a link error, not a runtime
 * cost. malloc_monitor's own numbers are reported unconditionally and cover
 * the question this indicator exists for. */
#if (defined(MODULE_NEWLIB_SYSCALLS_DEFAULT) || defined(HAVE_HEAP_STATS)) && \
    !IS_USED(MODULE_MPALAND_PRINTF)
#define _HAVE_HEAP_STATS    1
extern void heap_stats(void);
#endif

/* Tier identification, resolved at compile time. These two strings are what
 * makes a captured log attributable to a crypto tier (CRYPTO_TIERS.md §1):
 * the signature axis and the key-establishment axis are selected
 * independently, so both are always printed. */
#if IS_USED(MODULE_SUIT_ALGO_MLDSA44)
#define _SIG_ALGO       "ml-dsa-44"
#define _SIG_PUBKEY_LEN (1312)
#elif IS_USED(MODULE_SUIT_ALGO_MLDSA65)
#define _SIG_ALGO       "ml-dsa-65"
#define _SIG_PUBKEY_LEN (1952)
#elif IS_USED(MODULE_SUIT_ALGO_MLDSA87)
#define _SIG_ALGO       "ml-dsa-87"
#define _SIG_PUBKEY_LEN (2592)
#elif IS_USED(MODULE_SUIT_ALGO_ES256)
#define _SIG_ALGO       "es256"
#define _SIG_PUBKEY_LEN (64)    /* x||y, see handlers_envelope.c */
#elif IS_USED(MODULE_SUIT_ALGO_ES384)
#define _SIG_ALGO       "es384"
#define _SIG_PUBKEY_LEN (96)
#elif IS_USED(MODULE_SUIT_ALGO_ES512)
#define _SIG_ALGO       "es512"
#define _SIG_PUBKEY_LEN (132)
#else
#define _SIG_ALGO       "ed25519"
#define _SIG_PUBKEY_LEN (32)
#endif

#ifndef MODULE_SUIT_MANIFEST_ENCRYPT
#define _KEM_ALGO   "none"
#elif defined(MODULE_WOLFCRYPT_MLKEM1024)
#define _KEM_ALGO   "ml-kem-1024"
#elif defined(MODULE_WOLFCRYPT_MLKEM)
#define _KEM_ALGO   "ml-kem-768"
#else
#define _KEM_ALGO   "x25519"
#endif

#ifdef SUIT_PQ_SCRATCH_SHARED
#define _PQ_SCRATCH_LEN sizeof(union suit_pq_scratch)
#else
#define _PQ_SCRATCH_LEN (0)     /* no shared union in this build */
#endif

#ifdef MODULE_SUIT_FIRMWARE_ENCRYPT
#define _FW_HDR_LEN     SUIT_FW_ENC_HDR_LEN
#else
#define _FW_HDR_LEN     (0)
#endif

typedef struct {
    uint32_t start;     /**< timestamp of the open begin(), if any */
    uint32_t us;        /**< accumulated time */
    uint32_t bytes;     /**< accumulated bytes */
    uint32_t calls;     /**< number of completed begin/end pairs */
    uint32_t chunks;    /**< number of suit_perf_count() events */
} suit_perf_rec_t;

/* Index-parallel to suit_perf_phase_t. Kept short and lowercase: these are
 * CSV field values, not prose. */
static const char *const _names[SUIT_PERF_NUMOF] = {
    [SUIT_PERF_TOTAL]           = "total",
    [SUIT_PERF_MFST_FETCH]      = "mfst_fetch",
    [SUIT_PERF_MFST_COSE]       = "mfst_cose",
    [SUIT_PERF_MFST_KEM]        = "mfst_kem",
    [SUIT_PERF_MFST_AEAD]       = "mfst_aead",
    [SUIT_PERF_PARSE]           = "parse",
    [SUIT_PERF_SIG_VERIFY]      = "sig_verify",
    [SUIT_PERF_MFST_DIGEST]     = "mfst_digest",
    [SUIT_PERF_PAYLOAD_FETCH]   = "payload_fetch",
    [SUIT_PERF_PAYLOAD_KEM]     = "payload_kem",
    [SUIT_PERF_PAYLOAD_AEAD]    = "payload_aead",
    [SUIT_PERF_STORAGE_WRITE]   = "storage_write",
    [SUIT_PERF_IMAGE_DIGEST]    = "image_digest",
    [SUIT_PERF_HDR_VALIDATE]    = "hdr_validate",
};

static suit_perf_rec_t _rec[SUIT_PERF_NUMOF];
static uint32_t _run;

/* Worker stack, handed over by sys/suit/transport/worker.c (the buffer is
 * static and private there). NULL until the first run, which is why every
 * sampler is guarded. */
static char *_stack;
static size_t _stack_size;
static uint32_t _stack_used_max;
static suit_perf_phase_t _stack_peak_phase;

void suit_perf_run_start(char *stack, size_t stack_size)
{
    memset(_rec, 0, sizeof(_rec));
    _stack = stack;
    _stack_size = stack_size;
    _stack_used_max = 0;
    _stack_peak_phase = SUIT_PERF_TOTAL;
    _run++;
    malloc_monitor_reset_high_watermark();
}

void suit_perf_begin(suit_perf_phase_t phase)
{
    _rec[phase].start = ztimer_now(ZTIMER_USEC);
}

void suit_perf_end(suit_perf_phase_t phase)
{
    /* unsigned wrap-around makes this correct across a single timer
     * rollover; a phase longer than the ~71 min ZTIMER_USEC period is not
     * representable and is out of scope (PERFORMANCE.md states the limit) */
    _rec[phase].us += ztimer_now(ZTIMER_USEC) - _rec[phase].start;
    _rec[phase].calls++;
}

void suit_perf_count(suit_perf_phase_t phase, size_t bytes)
{
    _rec[phase].bytes += bytes;
    _rec[phase].chunks++;
}

void suit_perf_stack_sample(suit_perf_phase_t phase)
{
    if (_stack == NULL) {
        return;
    }

    /* The canary this reads is written by thread_create() and requires
     * SCHED_TEST_STACK (set by the app's SUIT_PERF=1 block); without it the
     * result is meaningless rather than wrong-by-a-little. The worker thread
     * is re-created per update, so the canary is fresh every run. */
    uint32_t used = _stack_size - measure_stack_free_internal(_stack, _stack_size);

    if (used > _stack_used_max) {
        _stack_used_max = used;
        _stack_peak_phase = phase;
    }
}

void suit_perf_report(void)
{
    if (_run == 0) {
        /* Reached by the shell command on a freshly booted image -- notably
         * the slot the last successful update rebooted into, where the
         * counters are legitimately empty. Say so rather than print a table
         * of zeroes that could be mistaken for a measurement. */
        puts("suit_perf: no update run recorded since boot");
        return;
    }

    /* sample before printing: printf() itself runs on this stack */
    suit_perf_stack_sample(SUIT_PERF_TOTAL);

    size_t heap_max = malloc_monitor_get_usage_high_watermark();
    size_t heap_now = malloc_monitor_get_usage_current();

    printf("suit_perf: run %" PRIu32 "  sig=%s kem=%s\n",
           _run, _SIG_ALGO, _KEM_ALGO);
    puts("  phase          |  time [us] |     bytes | calls | chunks");

    for (unsigned i = 0; i < SUIT_PERF_NUMOF; i++) {
        printf("  %-14s | %10" PRIu32 " | %9" PRIu32 " | %5" PRIu32
               " | %6" PRIu32 "\n",
               _names[i], _rec[i].us, _rec[i].bytes, _rec[i].calls,
               _rec[i].chunks);
    }

    printf("  stack peak: %" PRIu32 " of %" PRIuSIZE " B (%s), heap peak: %"
           PRIuSIZE " B, heap now: %" PRIuSIZE " B\n",
           _stack_used_max, _stack_size, _names[_stack_peak_phase],
           heap_max, heap_now);

    /* machine-readable mirror: `grep '^PERF' term.log` is the whole dataset.
     * Every phase is emitted even when unused, so the CSV stays rectangular
     * across tiers and combinations. */
    for (unsigned i = 0; i < SUIT_PERF_NUMOF; i++) {
        printf("PERF,%" PRIu32 ",%s,%s,%s,%" PRIu32 ",%" PRIu32 ",%" PRIu32
               ",%" PRIu32 "\n",
               _run, _SIG_ALGO, _KEM_ALGO, _names[i],
               _rec[i].us, _rec[i].bytes, _rec[i].calls, _rec[i].chunks);
    }
    printf("PERFMEM,%" PRIu32 ",stack_max_used,%" PRIu32 ",%" PRIuSIZE ",%s\n",
           _run, _stack_used_max, _stack_size, _names[_stack_peak_phase]);
    printf("PERFMEM,%" PRIu32 ",heap_hwm,%" PRIuSIZE "\n", _run, heap_max);
    printf("PERFMEM,%" PRIu32 ",heap_now,%" PRIuSIZE "\n", _run, heap_now);

#ifdef _HAVE_HEAP_STATS
    heap_stats();
#endif
}

void suit_perf_config_report(void)
{
    printf("suit_perf: %s sig=%s kem=%s pubkey=%u B\n",
           RIOT_BOARD, _SIG_ALGO, _KEM_ALGO, (unsigned)_SIG_PUBKEY_LEN);
    printf("suit_perf: manifest_buf=%" PRIuSIZE " B fw_hdr=%u B pq_scratch=%"
           PRIuSIZE " B worker_stack=%" PRIuSIZE " B\n",
           suit_perf_manifest_bufsize, (unsigned)_FW_HDR_LEN,
           (size_t)_PQ_SCRATCH_LEN, suit_perf_worker_stacksize);
    printf("PERFCFG,%s,%s,%s,%" PRIuSIZE ",%u,%" PRIuSIZE ",%" PRIuSIZE ",%u\n",
           RIOT_BOARD, _SIG_ALGO, _KEM_ALGO, suit_perf_manifest_bufsize,
           (unsigned)_FW_HDR_LEN, (size_t)_PQ_SCRATCH_LEN,
           suit_perf_worker_stacksize, (unsigned)_SIG_PUBKEY_LEN);
}

#else
typedef int dont_be_pedantic;
#endif /* MODULE_SUIT_PERF */
