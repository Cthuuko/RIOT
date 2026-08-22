#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared statistics and CSV emission for the two SUIT perf harnesses.

`host_crypto_bench.py` (producer, in-process repeats) and `device_perf_bench.py`
(consumer, one repeat per OTA update) must report the *same* summary of a
sample set, or the spreads quoted in PERF_RESULTS_HOST.md and
PERF_RESULTS_NRF52840.md are only coincidentally comparable. Both import from
here so there is exactly one percentile convention in the project.

The `HOSTBENCH,` and `DEVBENCH,` line formats are identical apart from the
prefix, so the same parsing works on either.
"""

import statistics

__all__ = ['summarise', 'bench_header', 'bench_line', 'format_row']

BENCH_FIELDS = ('tier', 'sig_algo', 'kem_algo', 'phase', 'n', 'median_us',
                'min_us', 'p95_us', 'stdev_us', 'bytes')


def summarise(samples):
    """Reduce a sample set to the five figures both documents quote.

    Percentiles are nearest-rank on the sorted samples rather than
    interpolated: with N as low as 20 (one OTA update each on the device side)
    an interpolated p95 would invent a value between two real measurements.
    """
    ordered = sorted(samples)
    n = len(ordered)
    if n == 0:
        return {'n': 0, 'median': 0.0, 'min': 0.0, 'p95': 0.0, 'stdev': 0.0}
    p95 = ordered[min(n - 1, int(round(0.95 * (n - 1))))]
    return {
        'n': n,
        'median': statistics.median(ordered),
        'min': ordered[0],
        'p95': p95,
        'stdev': statistics.stdev(ordered) if n > 1 else 0.0,
    }


def constant(value_bytes):
    """A 'phase' that carries only a size, e.g. a signature or header length.

    Emitted with zero timing so the CSV stays rectangular across phases, the
    same way sys/suit/perf.c emits every phase on every run.
    """
    return {'n': 1, 'median': 0.0, 'min': 0.0, 'p95': 0.0, 'stdev': 0.0}, \
        value_bytes


def bench_header(prefix):
    return prefix + ',' + ','.join(BENCH_FIELDS)


def bench_line(prefix, tier, sig, kem, phase, stats, nbytes):
    return '{},{},{},{},{},{},{:.3f},{:.3f},{:.3f},{:.3f},{}'.format(
        prefix, tier, sig, kem, phase, stats['n'], stats['median'],
        stats['min'], stats['p95'], stats['stdev'], nbytes)


def format_row(tier, phase, sig, kem, stats, nbytes):
    """One line of the human-readable table that accompanies the CSV.

    The numeric fields are 13 wide because the same table serves both
    harnesses: host phases are single-digit microseconds, device phases are
    whole seconds expressed in them (~1.3e7 us for a `total`).
    """
    return ('  {:<4} {:<14} {:<20} {:>13.3f} {:>13.3f} {:>13.3f} {:>11.3f} '
            '{:>10}'.format(tier, phase, f'{sig}/{kem}', stats['median'],
                            stats['min'], stats['p95'], stats['stdev'],
                            nbytes))


def format_header():
    return ('  tier phase          sig/kem                '
            'median [us]       min [us]       p95 [us]  stdev [us]      bytes')
