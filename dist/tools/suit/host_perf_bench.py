#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeat-measurement harness for the *real* producer pipeline.

host_crypto_bench.py already measures the producer's crypto, but it measures it
in isolation: each primitive re-implemented in-process and run over fixed
reference bytes. That is the right way to compare algorithms, and the wrong way
to answer "what does publishing an update cost", because it leaves out
everything else a publish pays for -- manifest construction and CBOR
serialisation, the real 109 KB firmware payload rather than a reference buffer,
file I/O, and one Python interpreter start per tool.

This is the producer-side counterpart of device_perf_bench.py. It runs the
genuine `make suit/publish` and `encrypt_manifest.py` N times and reports the
distribution of what the instrumented tools recorded (hostperf.py, via
SUIT_HOST_PERF=1), alongside the wall-clock each process actually took. The
tier table is imported from device_perf_bench rather than restated, so the two
harnesses cannot drift apart in what they mean by "T3".

    python3 dist/tools/suit/host_perf_bench.py --tier T1 --repeats 20

Numbers pair with PERF_RESULTS_HOST.md's primitive-level ones: where a phase
appears in both, the difference between them is the pipeline overhead around
that primitive.
"""

import argparse
import collections
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfstats import (            # noqa: E402  (path set above)
    bench_header, bench_line, format_header, format_row, summarise,
)
# TIERS/APPDIR/RIOTBASE come from the device harness so there is one definition
# of each tier in the project. It costs a pyserial import on a host-only tool,
# which is the cheaper of the two prices.
import device_perf_bench as dpb    # noqa: E402

BENCH_PREFIX = 'HOSTPIPE'

# HOSTPERF,<tool>,<sig>,<kem>,<phase>,<us>,<bytes>,<calls>,<chunks>
HOSTPERF_FIELDS = 9


def parse_hostperf(path):
    """Fold one iteration's log into {(tool, phase): [us, bytes, procs]}.

    One publish is several processes and hostperf.py appends all of them to the
    same file, so `encrypt-firmware` legitimately appears twice -- once per slot
    binary. Summing within the iteration is deliberate: the question this
    harness answers is what a whole publish costs, and both slots are part of
    it. `procs` keeps that visible rather than implicit.
    """
    totals = collections.OrderedDict()
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return totals

    for line in lines:
        parts = line.strip().split(',')
        if len(parts) != HOSTPERF_FIELDS or parts[0] != 'HOSTPERF':
            continue
        _, tool, _sig, _kem, phase, us, nbytes, _calls, _chunks = parts
        key = (tool, phase)
        acc = totals.setdefault(key, [0, 0, 0])
        acc[0] += int(us)
        acc[1] += int(nbytes)
        acc[2] += 1
    return totals


def publish(tier, board, app_ver, coap_server, log_path):
    """One `make suit/publish`, instrumented. Returns wall-clock microseconds.

    RIOTBOOT_SKIP_COMPILE=1 skips the bootloader build; the riotboot header is
    regenerated from APP_VER regardless, which is what a repeat loop needs.
    """
    env = os.environ.copy()
    env.update({
        'BOARD': board,
        'SUIT_PERF': '1',
        'PROGRESS_BAR': '0',
        'RIOTBOOT_SKIP_COMPILE': '1',
        'APP_VER': str(app_ver),
        'SUIT_COAP_SERVER': coap_server,
        'SUIT_KEY_DIR': os.path.join(dpb.APPDIR, tier.keydir),
        'SUIT_KEY': tier.key,
        'SUIT_HOST_PERF': '1',
        'SUIT_HOST_PERF_LOG': log_path,
    })
    for var, val in (('SUIT_KEY_ALGO', tier.key_algo),
                     ('SUIT_MANIFEST_ENCRYPT_ALGO', tier.enc_algo)):
        if val:
            env[var] = val
        else:
            env.pop(var, None)

    t0 = time.perf_counter()
    subprocess.run(['make', '-C', dpb.APPDIR, 'suit/publish'],
                   env=env, check=True, stdout=subprocess.DEVNULL)
    return (time.perf_counter() - t0) * 1e6


def encrypt_manifest(tier, board, coap_root, log_path):
    """Wrap the signed manifest for the tier's device key. Wall-clock us."""
    base = os.path.join(coap_root, 'fw', 'suit_update', board)
    env = os.environ.copy()
    env.update({'SUIT_HOST_PERF': '1', 'SUIT_HOST_PERF_LOG': log_path})

    t0 = time.perf_counter()
    subprocess.run(
        ['python3', os.path.join(dpb.APPDIR, tier.enctool),
         '--key', os.path.join(dpb.APPDIR, tier.keydir, tier.devkey),
         '-o', os.path.join(base, 'riot.suit.enc'),
         os.path.join(base, 'riot.suit.latest.bin')],
        env=env, check=True, stdout=subprocess.DEVNULL)
    return (time.perf_counter() - t0) * 1e6


def run_once(tier, args, app_ver, scratch):
    """One full publish + encrypt, returning per-phase microseconds."""
    if os.path.exists(scratch):
        os.unlink(scratch)          # per-iteration log: hostperf.py appends

    pub_us = publish(tier, args.board, app_ver, args.coap_server, scratch)
    enc_us = encrypt_manifest(tier, args.board, args.coap_root, scratch)

    phases = parse_hostperf(scratch)
    # Wall-clock alongside the instrumented phases: the gap between them is
    # interpreter startup, make's own traversal and file I/O, which is most of
    # what separates this harness from host_crypto_bench.py.
    phases[('pipeline', 'publish_wall')] = [pub_us, 0, 1]
    phases[('pipeline', 'encrypt_wall')] = [enc_us, 0, 1]
    phases[('pipeline', 'total_wall')] = [pub_us + enc_us, 0, 1]
    return phases


def preflight(args, tier):
    problems = []

    if not os.path.isdir(args.coap_root):
        problems.append(f'CoAP root {args.coap_root} does not exist')

    for rel in (tier.keydir + '/' + tier.key + '.pem',
                tier.keydir + '/' + tier.devkey):
        if not os.path.exists(os.path.join(dpb.APPDIR, rel)):
            problems.append(f'missing key {rel}')

    # Both harnesses publish into the same coaproot: running them at once means
    # each overwrites the manifest the other just published, which corrupts the
    # device sweep rather than this one.
    for pid, cmd in _running_device_benches():
        problems.append(
            f'device_perf_bench.py is running (pid {pid}) and publishes into '
            f'the same {args.coap_root}.\n    Wait for it to finish, or pass '
            f'--force if you know the two cannot collide.\n    {cmd}')

    return problems


def _running_device_benches():
    import glob
    me = os.getpid()
    for entry in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            pid = int(entry.split('/')[2])
            if pid == me:
                continue
            with open(entry, 'rb') as fh:
                cmd = fh.read().replace(b'\0', b' ').decode(
                    'utf-8', errors='replace').strip()
        except (OSError, ValueError):
            continue
        if 'device_perf_bench.py' in cmd and 'python' in cmd:
            yield pid, cmd


def aggregate(runs):
    """Per-phase distribution across iterations, preserving first-seen order."""
    order = []
    for r in runs:
        for key in r:
            if key not in order:
                order.append(key)

    rows = []
    for key in order:
        samples = [r[key][0] for r in runs if key in r]
        sizes = {r[key][1] for r in runs if key in r}
        nbytes = sizes.pop() if len(sizes) == 1 else -1
        rows.append((key, summarise(samples), nbytes, len(sizes) > 0))
    return rows


def main(args):
    tier = dpb.TIERS[args.tier]
    print(f'# host_perf_bench: tier={args.tier} sig={tier.sig} '
          f'kem={tier.kem} board={args.board} repeats={args.repeats} '
          f'warmup={args.warmup}')

    problems = preflight(args, tier)
    if problems and not args.force:
        print('\npre-flight failed:\n', file=sys.stderr)
        for p in problems:
            print(f'  - {p}\n', file=sys.stderr)
        return 1
    for p in problems:
        print(f'# WARNING (forced past): {p.splitlines()[0]}')

    outfile = args.out or os.path.expanduser(
        f'~/suit-perf-logs/hostpipe-{args.tier}.csv')
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    scratch = outfile + '.iter'

    runs, failures = [], 0
    app_ver = int(time.time())
    with open(outfile, 'a') as raw_log:
        raw_log.write(f'# host_perf_bench {args.tier} {args.board} '
                      f'{time.strftime("%Y-%m-%dT%H:%M:%S")}\n')
        # The first publish after a tier change rebuilds the application, which
        # is minutes rather than seconds and is not what this measures.
        for i in range(1, args.repeats + args.warmup + 1):
            app_ver += 1
            warm = i <= args.warmup
            label = 'warmup' if warm else f'run {i - args.warmup}'
            try:
                t0 = time.perf_counter()
                phases = run_once(tier, args, app_ver, scratch)
                wall = time.perf_counter() - t0
            except subprocess.CalledProcessError as e:
                failures += 1
                print(f'  {label}: FAILED -- {e}', file=sys.stderr)
                continue

            print(f'  {label}: publish={phases[("pipeline", "publish_wall")][0]/1e6:.3f} s'
                  f'  encrypt={phases[("pipeline", "encrypt_wall")][0]/1e6:.3f} s'
                  f'  total={wall:.3f} s', flush=True)
            if warm:
                continue

            runs.append(phases)
            for (tool, phase), (us, nbytes, procs) in phases.items():
                raw_log.write(f'HOSTPIPERAW,{args.tier},{tool},{phase},{us},'
                              f'{nbytes},{procs}\n')
            raw_log.write(f'# run {i - args.warmup} app_ver={app_ver}\n')
            raw_log.flush()

    if os.path.exists(scratch):
        os.unlink(scratch)

    if not runs:
        print('no successful runs', file=sys.stderr)
        return 1

    print(f'\n# {len(runs)} successful runs, {failures} failed')
    print(f'# raw per-iteration lines: {outfile}')
    print()
    print(bench_header(BENCH_PREFIX))
    rows = aggregate(runs)
    for (tool, phase), stats, nbytes, varied in rows:
        print(bench_line(BENCH_PREFIX, args.tier, tier.sig, tier.kem,
                         f'{tool}:{phase}', stats, nbytes))
        if varied:
            print(f'# WARNING: {tool}:{phase} byte count varied between runs')

    print()
    print(format_header())
    for (tool, phase), stats, nbytes, _ in rows:
        print(format_row(args.tier, f'{tool}:{phase}'[:14], tier.sig, tier.kem,
                         stats, nbytes))
    return 0


def parse_arguments():
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__.split('\n\n')[0])
    p.add_argument('--tier', required=True, choices=sorted(dpb.TIERS),
                   help='which crypto tier to publish')
    p.add_argument('--repeats', type=int, default=20,
                   help='number of publishes to measure')
    p.add_argument('--warmup', type=int, default=1,
                   help='unmeasured publishes first; the one after a tier '
                        'change rebuilds the application')
    p.add_argument('--board', default='nrf52840dongle',
                   help='board to publish for; pairs with the device results')
    p.add_argument('--coap-server', default='[2001:db8::1]')
    p.add_argument('--coap-root', default=os.path.join(dpb.RIOTBASE, 'coaproot'))
    p.add_argument('--out', default=None,
                   help='raw log (default ~/suit-perf-logs/hostpipe-<tier>.csv)')
    p.add_argument('--force', action='store_true',
                   help='continue past pre-flight failures')
    return p.parse_args()


if __name__ == '__main__':
    sys.exit(main(parse_arguments()))
