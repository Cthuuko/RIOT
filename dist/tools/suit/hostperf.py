#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opt-in host-side performance checkpoints for the SUIT publish pipeline.

The device half of a SUIT update is instrumented by `sys/suit/perf.c`
(`SUIT_PERF=1`); this is the producer half — the build host that hashes,
**signs**, **encapsulates** and **encrypts**. Enabled by `SUIT_HOST_PERF=1` in
the environment; with the variable unset every entry point below is a no-op, so
an uninstrumented invocation of any tool behaves exactly as before and writes
nothing to stderr.

Phase names deliberately reuse the device's wherever the operation is the
counterpart (`sig_sign`↔`sig_verify`, `mfst_kem`↔`mfst_kem`, `mfst_aead`,
`payload_aead`, `mfst_digest`), so the two result documents can be read side by
side. The measurement taxonomy and the CSV schema are documented in
examples/advanced/suit_update/PERFORMANCE.md §8.

Usage contract:
  - `phase()` **accumulates**: a phase entered once per slot binary sums over
    the publish. Each `with` block increments the phase's `calls` counter.
  - `count()` accumulates bytes and increments a separate `chunks` counter, so
    a phase can be timed and counted at different granularities.
  - The report is emitted from an `atexit` hook, so a tool that returns early
    still reports what it did.

One update is one device process, but one publish is *several* host processes
(`suit-tool create`, `suit-tool sign`, `encrypt_firmware.py` twice,
`encrypt_manifest.py`). The `tool` field therefore takes the place of the
device schema's `run` field, and `SUIT_HOST_PERF_LOG` appends every process's
lines to one file so a whole publish lands in a single CSV.
"""

import atexit
import os
import sys
import time

__all__ = ['perf', 'HostPerf']

_ENV_ENABLE = 'SUIT_HOST_PERF'
_ENV_LOG = 'SUIT_HOST_PERF_LOG'


class _Timer:
    """Context manager returned by `HostPerf.phase()`.

    Kept as a real object rather than a `@contextmanager` generator so the
    disabled path can hand back a shared singleton and allocate nothing.
    """

    def __init__(self, owner, name, nbytes):
        self._owner = owner
        self._name = name
        self._bytes = nbytes

    def __enter__(self):
        self._start = time.perf_counter_ns()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter_ns() - self._start
        rec = self._owner._rec.setdefault(
            self._name, {'ns': 0, 'bytes': 0, 'calls': 0, 'chunks': 0})
        rec['ns'] += elapsed
        rec['calls'] += 1
        if self._bytes is not None:
            rec['bytes'] += self._bytes
            rec['chunks'] += 1
        return False


class _NullTimer:
    """`with` block that does nothing, for the disabled path."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


_NULL_TIMER = _NullTimer()


class HostPerf:
    """Per-process record table. One instance (`perf`) is the module API."""

    def __init__(self, enabled):
        self.enabled = enabled
        self._rec = {}
        self._order = []
        self._tool = os.path.basename(sys.argv[0]) or 'python'
        self._sig = 'none'
        self._kem = 'none'
        if enabled:
            self._t0 = time.perf_counter_ns()
            atexit.register(self.report)

    # -- configuration -----------------------------------------------------

    def set_tool(self, name):
        """Name this process in the `tool` CSV field (e.g. `suit-tool-sign`)."""
        if self.enabled:
            self._tool = name

    def set_algos(self, sig=None, kem=None):
        """Record which tier this process is producing for.

        Both axes are always printed, as on the device: the signature and the
        key-establishment algorithm are selected independently, and a log is
        only attributable to a tier if it states both.
        """
        if not self.enabled:
            return
        if sig is not None:
            self._sig = sig
        if kem is not None:
            self._kem = kem

    # -- measurement -------------------------------------------------------

    def phase(self, name, nbytes=None):
        """Time a block, adding the elapsed time to @p name's accumulator."""
        if not self.enabled:
            return _NULL_TIMER
        if name not in self._rec:
            self._order.append(name)
        return _Timer(self, name, nbytes)

    def count(self, name, nbytes):
        """Add bytes to a phase without timing it."""
        if not self.enabled:
            return
        if name not in self._rec:
            self._order.append(name)
        rec = self._rec.setdefault(
            name, {'ns': 0, 'bytes': 0, 'calls': 0, 'chunks': 0})
        rec['bytes'] += nbytes
        rec['chunks'] += 1

    # -- reporting ---------------------------------------------------------

    def _config_line(self):
        try:
            import cryptography
            cryptography_ver = cryptography.__version__
        except Exception:
            cryptography_ver = 'unknown'
        return 'HOSTCFG,{},{},{},{},{},{}'.format(
            self._tool, self._sig, self._kem,
            '.'.join(str(v) for v in sys.version_info[:3]),
            cryptography_ver, _cpu_model())

    def report(self):
        """Emit HOSTCFG + one HOSTPERF line per phase. Idempotent."""
        if not self.enabled:
            return
        self.enabled = False        # atexit may fire after an explicit call

        total_us = (time.perf_counter_ns() - self._t0) // 1000
        lines = [self._config_line()]
        for name in self._order:
            rec = self._rec[name]
            lines.append('HOSTPERF,{},{},{},{},{},{},{},{}'.format(
                self._tool, self._sig, self._kem, name,
                rec['ns'] // 1000, rec['bytes'], rec['calls'], rec['chunks']))
        # `total` is the process, measured from module import to exit: it
        # includes interpreter startup after this module loads, argument
        # parsing and file I/O, which is what makes it the *pipeline* cost
        # rather than the sum of the crypto phases. The difference between the
        # two is the residual reported in PERF_RESULTS_HOST.md.
        lines.append('HOSTPERF,{},{},{},total,{},0,1,0'.format(
            self._tool, self._sig, self._kem, total_us))

        blob = '\n'.join(lines) + '\n'
        sys.stderr.write(blob)
        sys.stderr.flush()

        logfile = os.environ.get(_ENV_LOG)
        if logfile:
            # Append: one publish spans several processes and they all land in
            # the same per-tier CSV.
            try:
                with open(logfile, 'a') as f:
                    f.write(blob)
            except OSError as e:
                sys.stderr.write(
                    'hostperf: cannot write {}: {}\n'.format(logfile, e))


def _cpu_model():
    """Best-effort CPU name for the config line; never raises."""
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('model name'):
                    return line.split(':', 1)[1].strip().replace(',', ' ')
    except OSError:
        pass
    return 'unknown'


perf = HostPerf(os.environ.get(_ENV_ENABLE) == '1')
