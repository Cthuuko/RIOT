/*
 * Copyright (C) 2026 Miguel Arcilla
 *
 * This file is subject to the terms and conditions of the GNU Lesser
 * General Public License v2.1. See the file LICENSE in the top level
 * directory for more details.
 */

/**
 * @ingroup     sys_suit
 * @brief       Opt-in performance and memory checkpoints for the SUIT update
 *
 * Enabled by the `suit_perf` pseudomodule (`SUIT_PERF=1` in
 * examples/advanced/suit_update). When it is absent every entry point below
 * collapses to `((void)0)`, so the instrumented sources need no `#ifdef` at
 * the call sites and an uninstrumented build is byte-identical to one without
 * this header.
 *
 * The phases are grouped into the operation classes described in
 * examples/advanced/suit_update/PERFORMANCE.md: secret-value generation and
 * signature verification vary with the crypto tier (classical / hybrid /
 * post-quantum), everything else is tier-invariant and serves as the control
 * that makes the tier deltas readable.
 *
 * Usage contract:
 *  - @ref suit_perf_run_start starts a run and hands over the worker stack;
 *    only the owner of that stack (sys/suit/transport/worker.c) may call it.
 *  - @ref suit_perf_begin / @ref suit_perf_end **accumulate**: a phase entered
 *    once per transport block sums up over the transfer. They must not nest on
 *    the same phase. Each pair increments the phase's `calls` counter.
 *  - @ref suit_perf_count accumulates bytes and increments a separate `chunks`
 *    counter, so a phase can be timed and counted at different granularities
 *    (the payload transfer is timed once but counted per block).
 *  - @ref suit_perf_report must run before the update's completion callback,
 *    which reboots the board on success.
 *
 * @{
 *
 * @file
 * @author      Miguel Arcilla <miguelkristopharcilla@gmail.com>
 */
#ifndef SUIT_PERF_H
#define SUIT_PERF_H

#include <stddef.h>
#include <stdint.h>

#include "kernel_defines.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Instrumented phases of a SUIT update
 *
 * Ordered as they occur; the report prints them in this order so a log reads
 * chronologically. The letters refer to the operation classes in
 * PERFORMANCE.md.
 */
typedef enum {
    SUIT_PERF_TOTAL,            /**< trigger to completion (envelope) */
    SUIT_PERF_MFST_FETCH,       /**< E: manifest transfer */
    SUIT_PERF_MFST_COSE,        /**< COSE_Encrypt container parsing */
    SUIT_PERF_MFST_KEM,         /**< A: manifest CEK derivation */
    SUIT_PERF_MFST_AEAD,        /**< C: one-shot manifest decryption */
    SUIT_PERF_PARSE,            /**< manifest processing (envelope) */
    SUIT_PERF_SIG_VERIFY,       /**< B: COSE signature verification */
    SUIT_PERF_MFST_DIGEST,      /**< D: manifest SHA-256 */
    SUIT_PERF_PAYLOAD_FETCH,    /**< E: payload transfer */
    SUIT_PERF_PAYLOAD_KEM,      /**< A: payload CEK derivation */
    SUIT_PERF_PAYLOAD_AEAD,     /**< C: streaming payload decryption */
    SUIT_PERF_STORAGE_WRITE,    /**< F: storage backend writes */
    SUIT_PERF_IMAGE_DIGEST,     /**< D: stored-image SHA-256 read-back */
    SUIT_PERF_HDR_VALIDATE,     /**< riotboot header validation */
    SUIT_PERF_NUMOF             /**< number of phases */
} suit_perf_phase_t;

#if IS_USED(MODULE_SUIT_PERF) || defined(DOXYGEN)

/**
 * @brief   Manifest buffer size the worker was built with
 *
 * Defined by sys/suit/transport/worker.c via
 * @ref SUIT_PERF_DEFINE_WORKER_SIZES so the boot-time report states the real
 * value instead of re-deriving the default.
 */
extern const size_t suit_perf_manifest_bufsize;

/**
 * @brief   Worker stack size the worker was built with
 */
extern const size_t suit_perf_worker_stacksize;

/**
 * @brief   Export the worker's compile-time sizes to the perf module
 *
 * Expands to nothing when the module is disabled. Invoke at file scope.
 */
#define SUIT_PERF_DEFINE_WORKER_SIZES(bufsize, stacksize)   \
    const size_t suit_perf_manifest_bufsize = (bufsize);    \
    const size_t suit_perf_worker_stacksize = (stacksize)

/**
 * @brief   Begin a run: clear all counters, take over stack accounting
 *
 * @param[in] stack         base of the SUIT worker thread's stack
 * @param[in] stack_size    size of that stack in bytes
 */
void suit_perf_run_start(char *stack, size_t stack_size);

/**
 * @brief   Start timing @p phase
 */
void suit_perf_begin(suit_perf_phase_t phase);

/**
 * @brief   Stop timing @p phase, adding the elapsed time to its accumulator
 */
void suit_perf_end(suit_perf_phase_t phase);

/**
 * @brief   Add @p bytes to the byte counter of @p phase
 */
void suit_perf_count(suit_perf_phase_t phase, size_t bytes);

/**
 * @brief   Sample the worker stack high-water mark, attributing a new peak
 *          to @p phase
 */
void suit_perf_stack_sample(suit_perf_phase_t phase);

/**
 * @brief   Print the report for the run that just finished
 *
 * Emits a human-readable table followed by the machine-readable `PERF,` /
 * `PERFMEM,` lines documented in PERFORMANCE.md.
 */
void suit_perf_report(void);

/**
 * @brief   Print the static, tier-identifying build configuration
 *
 * Called once at startup so every captured log says which tier produced it.
 */
void suit_perf_config_report(void);

#else /* !MODULE_SUIT_PERF */

#define SUIT_PERF_DEFINE_WORKER_SIZES(bufsize, stacksize) \
    typedef int suit_perf_sizes_unused_t

#define suit_perf_run_start(...)        ((void)0)
#define suit_perf_begin(...)            ((void)0)
#define suit_perf_end(...)              ((void)0)
#define suit_perf_count(...)            ((void)0)
#define suit_perf_stack_sample(...)     ((void)0)
#define suit_perf_report(...)           ((void)0)
#define suit_perf_config_report(...)    ((void)0)

#endif /* MODULE_SUIT_PERF */

#ifdef __cplusplus
}
#endif

#endif /* SUIT_PERF_H */
/** @} */
