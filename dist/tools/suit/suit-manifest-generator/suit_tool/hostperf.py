# -*- coding: utf-8 -*-
"""Package-local shim onto RIOT's dist/tools/suit/hostperf.py.

`suit-tool` is its own installable package, so it cannot import the shared
host-side perf module by name. This module locates it two directories up
(`suit-manifest-generator/suit_tool/` -> `dist/tools/suit/`) and re-exports its
`perf` singleton, falling back to a silent no-op when the file is not there —
which is the case whenever suit-tool is installed standalone outside the RIOT
tree.

Instrumentation is opt-in via `SUIT_HOST_PERF=1`; see
examples/advanced/suit_update/PERFORMANCE.md §8.
"""

import os
import sys
from contextlib import nullcontext

__all__ = ['perf']

_RIOT_SUIT_TOOLS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..'))

if _RIOT_SUIT_TOOLS not in sys.path:
    sys.path.append(_RIOT_SUIT_TOOLS)

try:
    from hostperf import perf
except ImportError:
    class _NoPerf:
        def phase(self, *args, **kwargs):
            return nullcontext()

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    perf = _NoPerf()
