#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unattended repeat-measurement harness for the device half of a SUIT update.

PERF_RESULTS_NRF52840.md was captured by hand, **one run per tier**, because a
successful update reboots the dongle through a USB re-enumeration that kills
picocom. That left its noise floor extrapolated from a single pair of Ed25519
measurements, T1's `payload_aead` outlier unexplained, and T4's `pq_scratch`
never captured at all.

This harness runs N updates back to back on an already-flashed tier and
survives every reboot by reopening the port itself, which is the only thing the
manual procedure could not do. It is the device-side counterpart of
host_perf_bench.py and reports through the same perfstats module, so the
spreads in the two results documents are directly comparable.

Everything in the loop is sudo-free. Flashing a tier and bringing the CDC-ECM
link up are *not* -- they need root, and are the one-time steps the pre-flight
checks for and prints commands for.

    python3 dist/tools/suit/device_perf_bench.py --tier T4 --repeats 20

See examples/advanced/suit_update/PERFORMANCE.md section 9.
"""

import argparse
import collections
import glob
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perfstats import (            # noqa: E402  (path set above)
    bench_header, bench_line, format_header, format_row, summarise,
)

try:
    import serial
except ImportError:
    raise SystemExit('device_perf_bench needs pyserial: pip3 install --user pyserial')

BENCH_PREFIX = 'DEVBENCH'

RIOTBASE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
APPDIR = os.path.join(RIOTBASE, 'examples', 'advanced', 'suit_update')

# sys/include/suit/perf.h: SUIT_PERF_NUMOF. Every phase is emitted on every
# run, so a complete report is exactly this many PERF lines plus 3 PERFMEM.
PERF_PHASES = 14
# The three PERFMEM lines carry these five values between them; a report is
# only complete once all of them have arrived.
MEM_KEYS = frozenset(('stack_used', 'stack_size', 'stack_phase',
                      'heap_hwm', 'heap_now'))

MFST_ENC = 'manifest-encryption/encrypt_manifest.py'
MFST_ENC_MLKEM = 'manifest-encryption-mlkem/encrypt_manifest.py'

# The five tiers of PERF_RESULTS_NRF52840.md. Every key already exists in the
# repo; none of these needs a keygen step.
Tier = collections.namedtuple(
    'Tier', 'sig kem keydir key key_algo enc_algo devkey enctool')

TIERS = {
    'T1': Tier('ed25519', 'x25519', 'ed25519-keys', 'ed25519',
               None, None, 'device_x25519.pem', MFST_ENC),
    'T2': Tier('ed25519', 'ml-kem-768', 'ed25519-keys', 'ed25519',
               None, 'ml-kem-768', 'device_mlkem768.pem', MFST_ENC_MLKEM),
    'T3': Tier('ml-dsa-44', 'x25519', 'mldsa44-keys', 'mldsa44',
               'ml-dsa-44', None, 'device_x25519.pem', MFST_ENC),
    'T4': Tier('ml-dsa-65', 'ml-kem-768', 'mldsa-keys', 'mldsa65',
               'ml-dsa-65', 'ml-kem-768', 'device_mlkem768.pem', MFST_ENC_MLKEM),
    'T5': Tier('es256', 'x25519', 'es256-keys', 'es256',
               'es256', None, 'device_x25519.pem', MFST_ENC),
}

PERF_RE = re.compile(r'^PERF,(\d+),([^,]+),([^,]+),([^,]+),(\d+),(\d+),(\d+),(\d+)')
PERFMEM_STACK_RE = re.compile(r'^PERFMEM,(\d+),stack_max_used,(\d+),(\d+),(\S+)')
PERFMEM_KV_RE = re.compile(r'^PERFMEM,(\d+),(heap_hwm|heap_now),(\d+)')
PERFCFG_RE = re.compile(r'^PERFCFG,(.*)$')
SLOT_RE = re.compile(r'Running from slot (\d+)')
PING_RECV_RE = re.compile(r'(\d+) packets received')


# --------------------------------------------------------------------------
# console


class Console:
    """A serial console that expects to be yanked out from under itself.

    The dongle's shell is CDC-ACM on the target MCU itself, so every SUIT
    reboot destroys and recreates the tty. Reopening across that gap is the
    entire reason this harness exists.
    """

    def __init__(self, port, baud=115200, verbose=False):
        self.port_glob = port
        self.baud = baud
        self.verbose = verbose
        self.ser = None
        self.path = None
        self._buf = ''
        self.io_error = False

    def resolve_port(self):
        if not self.port_glob or self.port_glob == 'auto':
            candidates = sorted(glob.glob('/dev/ttyACM*'))
            if not candidates:
                return None
            if len(candidates) > 1 and self.path in candidates:
                return self.path      # keep the one we were already using
            return candidates[0]
        matches = sorted(glob.glob(self.port_glob))
        return matches[0] if matches else None

    def open(self, timeout=60):
        """Wait for the tty to exist and accept a connection."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            path = self.resolve_port()
            if path:
                try:
                    self.ser = serial.Serial(path, self.baud, timeout=0.2)
                    self.path = path
                    self._buf = ''
                    self.io_error = False
                    return path
                except (OSError, serial.SerialException) as e:
                    last = e
            time.sleep(0.3)
        raise TimeoutError(f'no usable console within {timeout}s (last: {last})')

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def reopen(self, timeout=90):
        """Reconnect after a reboot, waiting for the node to actually cycle."""
        self.close()
        # The node lingers for a moment after the device drops off the bus;
        # opening it in that window succeeds and then reads nothing forever.
        time.sleep(1.5)
        return self.open(timeout=timeout)

    def send(self, line, chunk=8, pace=0.01):
        """Write the line in paced chunks rather than one burst.

        RIOT's stdio_cdc_acm drops input into a small ring buffer and applies
        no back-pressure, so a ~72 byte `suit fetch <url>` written in one go
        loses its tail. The truncated remnant then sits in the shell's line
        buffer and gets completed by whatever is sent next -- observed as
        `downloading ".../ricurrent_slot"`, i.e. a fetch command spliced to the
        following command. ~90 ms per command, against a 13 s run.
        """
        if self.ser is None:
            raise RuntimeError('console not open')
        data = (line + '\r\n').encode()
        for i in range(0, len(data), chunk):
            self.ser.write(data[i:i + chunk])
            self.ser.flush()
            time.sleep(pace)

    def readline(self, timeout):
        """One line, or None on timeout. Tolerates the port disappearing."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                return line.rstrip('\r')
            try:
                # Wake on the first byte, then take everything buffered.
                # read(256) instead blocks for the whole serial timeout on any
                # chunk short of 256 B -- which is exactly the tail of a perf
                # report, left unread in the kernel buffer while the node
                # reboots out from under it and the tty is torn down.
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting else 1)
            except (OSError, serial.SerialException):
                self.io_error = True
                return None           # device went away: caller decides
            if chunk:
                self._buf += chunk.decode('utf-8', errors='replace')
        return None

    def vanished(self):
        """True once the tty is gone. The CDC-ACM endpoint lives on the target
        MCU, so a reset takes the device node down with it -- which is a reboot
        signal that, unlike the log line, cannot be lost in a TX buffer."""
        if self.io_error:
            return True
        return self.path is not None and not os.path.exists(self.path)

    def drain(self, seconds=0.5):
        end = time.time() + seconds
        while time.time() < end:
            self.readline(0.1)

    def log(self, line):
        if self.verbose and line:
            print(f'    | {line}', flush=True)


# --------------------------------------------------------------------------
# host-side steps (no sudo)


def publish(tier, app_ver, coap_server, env_extra=None):
    """Re-publish at a new sequence number without recompiling.

    RIOTBOOT_SKIP_COMPILE=1 skips only the bootloader build; the riotboot
    header is regenerated from APP_VER regardless (makefiles/boot/riotboot.mk
    marks %.hdr FORCE), which is exactly what a repeat loop needs.
    """
    env = os.environ.copy()
    env.update({
        'BOARD': 'nrf52840dongle',
        'SUIT_PERF': '1',
        'PROGRESS_BAR': '0',
        'RIOTBOOT_SKIP_COMPILE': '1',
        'APP_VER': str(app_ver),
        'SUIT_COAP_SERVER': coap_server,
        'SUIT_KEY_DIR': os.path.join(APPDIR, tier.keydir),
        'SUIT_KEY': tier.key,
    })
    # The matching rule: the publish flags must equal the flags the running
    # image was built with, or the device rejects what it is sent.
    for var, val in (('SUIT_KEY_ALGO', tier.key_algo),
                     ('SUIT_MANIFEST_ENCRYPT_ALGO', tier.enc_algo)):
        if val:
            env[var] = val
        else:
            env.pop(var, None)
    env.update(env_extra or {})
    subprocess.run(['make', '-C', APPDIR, 'suit/publish'],
                   env=env, check=True, stdout=subprocess.DEVNULL)


def encrypt_manifest(tier, coap_root, env_extra=None):
    """Wrap the signed manifest for this tier's device key."""
    base = os.path.join(coap_root, 'fw', 'suit_update', 'nrf52840dongle')
    env = os.environ.copy()
    env.update(env_extra or {})
    subprocess.run(
        ['python3', os.path.join(APPDIR, tier.enctool),
         '--key', os.path.join(APPDIR, tier.keydir, tier.devkey),
         '-o', os.path.join(base, 'riot.suit.enc'),
         os.path.join(base, 'riot.suit.latest.bin')],
        env=env, check=True, stdout=subprocess.DEVNULL)


def notify(coap_server, client):
    subprocess.run(['make', '-C', APPDIR, 'suit/notify',
                    f'SUIT_COAP_SERVER={coap_server}',
                    f'SUIT_CLIENT={client}',
                    'SUIT_NOTIFY_MANIFEST=riot.suit.enc'],
                   check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------
# one measured update


def collect_report(con, timeout, verbose):
    """Read until a complete PERF report has arrived, or time out.

    Returns (records, mem, raw_lines). `records` maps phase -> dict; a report
    is complete only when all 14 phases and 3 PERFMEM lines are present, so a
    truncated capture is detectable rather than silently short.
    """
    records, mem, raw = {}, {}, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Tested before the read, not after: the report's last line is a
        # PERFMEM one, and worker.c reboots immediately after printing it, so
        # the `update successful` line that used to end this loop is normally
        # eaten by the reset. Waiting for it timed out on complete reports.
        if len(records) >= PERF_PHASES and MEM_KEYS <= mem.keys():
            return records, mem, raw
        line = con.readline(1.0)
        if line is None:
            # Gone mid-report: the tail of the burst raced pm_reboot() and
            # lost. Nothing more is coming, so fail in ~1 s rather than
            # sitting out the whole timeout for a run already lost.
            if con.vanished():
                raise TimeoutError(
                    f'report truncated by reboot: {len(records)}/{PERF_PHASES} '
                    f'PERF lines, missing {sorted(MEM_KEYS - mem.keys())}')
            continue
        con.log(line)
        m = PERF_RE.match(line)
        if m:
            raw.append(line)
            records[m.group(4)] = {
                'run': int(m.group(1)), 'sig': m.group(2), 'kem': m.group(3),
                'us': int(m.group(5)), 'bytes': int(m.group(6)),
                'calls': int(m.group(7)), 'chunks': int(m.group(8)),
            }
            continue
        m = PERFMEM_STACK_RE.match(line)
        if m:
            raw.append(line)
            mem['stack_used'] = int(m.group(2))
            mem['stack_size'] = int(m.group(3))
            mem['stack_phase'] = m.group(4)
            continue
        m = PERFMEM_KV_RE.match(line)
        if m:
            raw.append(line)
            mem[m.group(2)] = int(m.group(3))
            continue
        if 'suit_worker: suit_parse() failed' in line or 'res=-' in line:
            raise RuntimeError(f'update failed on device: {line}')
    raise TimeoutError(
        f'incomplete report: {len(records)}/{PERF_PHASES} PERF lines, '
        f'missing {sorted(MEM_KEYS - mem.keys())}')


def wait_for_reboot(con, timeout, verbose):
    """Wait out the reset that follows a successful update.

    `rebooting...` is printed one statement before pm_reboot() and is usually
    lost with the USB buffer, so the tty disappearing counts as the reboot.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = con.readline(1.0)
        if line is None:
            if con.vanished():
                return True
            continue
        con.log(line)
        if 'rebooting' in line:
            return True
        if 'update successful' in line:
            continue
    return False


def wait_for_server_reachable(con, server, timeout):
    """Block until the node can actually reach the CoAP server.

    Deliberately not "until the node has a global address": gnrc_uhcpc.c:100
    refuses to start the uhcp client unless the node has *two* interfaces, and
    this board has one, so no prefix is ever requested and no global address
    ever appears. What set_interface_roles() does give it is fe80::2 and a
    default route via fe80::1, and the fetch rides that from a link-local
    source. The round trip is the only readiness signal that means anything.

    Worth the second or so it costs: triggering into a dead link gets a fetch
    that dies in CoAP retransmissions ~90 s later while holding _worker_lock,
    which then silently swallows the *next* run's trigger too.
    """
    addr = server.strip('[]')
    deadline = time.time() + timeout
    while time.time() < deadline:
        con.drain(0.3)
        con.send(f'ping -c 1 {addr}')
        end = time.time() + 5
        while time.time() < end:
            line = con.readline(0.5)
            if line is None:
                continue
            con.log(line)
            m = PING_RECV_RE.search(line)
            if m:
                if int(m.group(1)) > 0:
                    return True
                break           # replied but unreachable: back off and retry
        time.sleep(1)
    return False


def wait_for_worker_start(con, timeout=15):
    """Confirm the trigger took.

    shell/cmds/suit.c:51 throws away suit_worker_trigger()'s return value, so a
    trigger refused because a worker still holds the lock prints *nothing*.
    Without this the harness waits out its whole --timeout for a report that
    was never going to be printed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = con.readline(1.0)
        if line is None:
            if con.vanished():
                return False
            continue
        con.log(line)
        if 'suit_worker: downloading' in line:
            return True
    return False


def read_config(con, timeout=10):
    """`suit_perf` prints PERFCFG first -- the line the manual capture could
    never get, because the reboot killed the terminal before it appeared."""
    con.drain(0.3)
    con.send('suit_perf')
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = con.readline(1.0)
        if line is None:
            continue
        con.log(line)
        m = PERFCFG_RE.match(line)
        if m:
            return line
    return None


def read_slot(con, timeout=10):
    con.drain(0.3)
    con.send('current_slot')
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = con.readline(1.0)
        if line is None:
            continue
        m = SLOT_RE.search(line)
        if m:
            return int(m.group(1))
    return None


def run_once(con, tier, args, app_ver, iteration):
    print(f'  run {iteration}: publishing seqnr {app_ver} ...', flush=True)
    host_env = {}
    if args.host_perf_log:
        host_env = {'SUIT_HOST_PERF': '1',
                    'SUIT_HOST_PERF_LOG': args.host_perf_log}
    publish(tier, app_ver, args.coap_server, host_env)
    encrypt_manifest(tier, args.coap_root, host_env)

    url = (f'coap://{args.coap_server}/fw/suit_update/nrf52840dongle/'
           'riot.suit.enc')
    # Terminate any partial line a previously truncated write left in the
    # shell's buffer, so it cannot splice itself onto this run's commands.
    con.send('')
    con.drain(0.5)
    if not wait_for_server_reachable(con, args.coap_server, args.link_timeout):
        raise RuntimeError(
            f'node cannot reach {args.coap_server} after {args.link_timeout} s '
            '-- its default route points at fe80::1, so check that address is '
            'on the host CDC-ECM interface (is suit-ecm-keeper.sh running?)')

    if args.trigger == 'fetch':
        con.send(f'suit fetch {url}')
    else:
        notify(args.coap_server, args.client)

    if not wait_for_worker_start(con):
        # Nothing releases _worker_lock but the worker itself finishing, so a
        # wedged one fails every remaining run identically. Reboot is the only
        # way back, and an unattended sweep has to take it itself.
        print(f'  run {iteration}: trigger refused, rebooting the node',
              flush=True)
        con.send('reboot')
        if wait_for_reboot(con, args.reboot_timeout, args.verbose):
            con.reopen()
        raise RuntimeError(
            'trigger never started a worker. Either the command was truncated '
            'on the way in (see Console.send), or a previous fetch still holds '
            '_worker_lock -- both are silent, so this is the only way to see '
            'either. Node rebooted; retrying next run')

    records, mem, raw = collect_report(con, args.timeout, args.verbose)

    # worker.c prints every phase whether or not it ran, so a fetch that dies
    # in CoAP retransmissions still yields a *complete* report -- 14 phases, 3
    # PERFMEM lines, all zeros but `total` and `mfst_fetch`. Recording those is
    # how three 90 s timeouts got into the raw log looking like measurements.
    dead = [p for p in ('sig_verify', 'storage_write')
            if records.get(p, {}).get('us', 0) == 0]
    if dead:
        raise RuntimeError(
            f'update did not complete: {", ".join(dead)} never ran '
            f'(mfst_fetch={records["mfst_fetch"]["us"] / 1e6:.1f} s) -- the '
            f'device probably cannot reach {args.coap_server}')

    print(f'  run {iteration}: total={records["total"]["us"]/1e6:.3f} s  '
          f'sig_verify={records["sig_verify"]["us"]/1000:.1f} ms  '
          f'stack={mem.get("stack_used")}/{mem.get("stack_size")} B  '
          f'heap={mem.get("heap_hwm")} B', flush=True)

    rebooted = wait_for_reboot(con, args.reboot_timeout, args.verbose)
    if rebooted:
        con.reopen()
    cfg = read_config(con)
    slot = read_slot(con)
    return {'records': records, 'mem': mem, 'raw': raw, 'cfg': cfg,
            'slot': slot, 'seqnr': app_ver, 'rebooted': rebooted}


# --------------------------------------------------------------------------
# pre-flight


def console_holders(path):
    """Other processes with the console open, as [(pid, cmdline)].

    Nothing stops two processes opening the same tty, and each read() then
    steals bytes from the other. That surfaces as the instrumentation check
    below failing -- a picocom left running reads as "not flashed with
    SUIT_PERF=1", which is the wrong thing to go and fix.
    """
    holders = {}
    me = os.getpid()
    for entry in glob.glob('/proc/[0-9]*/fd/*'):
        try:
            if os.readlink(entry) != path:
                continue
            pid = int(entry.split('/')[2])
        except (OSError, ValueError):
            continue        # fd or process vanished mid-scan, or not ours
        if pid == me or pid in holders:
            continue
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                holders[pid] = f.read().replace(b'\0', b' ').decode(
                    'utf-8', errors='replace').strip() or f'pid {pid}'
        except OSError:
            holders[pid] = f'pid {pid}'
    return sorted(holders.items())


def ecm_link_problems():
    """Whether the dongle can reach the host, not just the other way round.

    fe80::1 is the load-bearing one and the easy one to miss. The node has no
    global address at all -- gnrc_uhcpc.c:100 skips the uhcp client on a
    single-interface node -- and instead gets fe80::2 plus a default route via
    fe80::1 from set_interface_roles(). Without fe80::1 on the host, the node
    has a route to nowhere: every fetch dies in CoAP retransmissions, and the
    device still prints a complete, all-zero report that reads like a
    successful measurement. The interface is destroyed on every
    re-enumeration, taking this and the sysctls with it.
    """
    iface = detect_ecm_iface()
    if iface is None:
        return ['no cdc_ether interface: the dongle is not enumerated']

    problems = []
    try:
        addrs = subprocess.run(['ip', '-6', 'addr', 'show', 'dev', iface],
                               capture_output=True, text=True,
                               check=False).stdout
    except OSError:
        addrs = ''
    if 'fe80::1/' not in addrs:
        problems.append(
            f'fe80::1 is not on {iface} -- it is the next hop of the node\'s '
            'only route off-link, so nothing it sends can reach the server')

    for knob, want in (('forwarding', '1'), ('accept_ra', '0')):
        try:
            with open(f'/proc/sys/net/ipv6/conf/{iface}/{knob}') as f:
                got = f.read().strip()
        except OSError:
            continue        # no such knob on this kernel; not worth failing on
        if got != want:
            problems.append(f'net.ipv6.conf.{iface}.{knob} is {got}, want {want}')

    if problems:
        problems.append(
            'the CDC-ECM link is only half configured; sudo '
            f'{os.path.join(RIOTBASE, "dist/tools/suit/suit-ecm-keeper.sh")} '
            'sets it up and restores it after every reboot')
    return problems


def preflight(args, tier):
    """Fail before run 1 rather than halfway through a sweep. Each failure
    prints the one command that fixes it -- including the sudo ones, which
    this harness never runs itself."""
    problems = []

    if not os.path.isdir(args.coap_root):
        problems.append(
            f'CoAP root {args.coap_root} missing.\n'
            f'    mkdir -p {args.coap_root} && aiocoap-fileserver {args.coap_root}')

    for rel in (tier.keydir + '/' + tier.key + '.pem',
                tier.keydir + '/' + tier.devkey):
        if not os.path.exists(os.path.join(APPDIR, rel)):
            problems.append(f'missing key {rel}')

    path = Console(args.port).resolve_port()
    if path:
        for pid, cmd in console_holders(path):
            problems.append(
                f'{path} is already open by pid {pid}: {cmd}\n'
                f'    Two readers split the byte stream between them. Close it\n'
                f'    (picocom: Ctrl-A Ctrl-X) -- this harness logs the console\n'
                f'    itself, so nothing is lost by not watching it.')

    problems.extend(ecm_link_problems())

    addr = args.coap_server.strip('[]')
    try:
        out = subprocess.run(['ip', '-6', 'addr'], capture_output=True,
                             text=True, check=False).stdout
    except OSError:
        out = ''
    if addr not in out:
        iface = detect_ecm_iface() or '$ECM_IFACE'
        problems.append(
            f'host address {addr} is not on any interface -- the CDC-ECM link\n'
            f'    is destroyed on every re-enumeration and re-adding it needs root:\n'
            f'    sudo ip address add {addr}/64 dev {iface}\n'
            f'    (better: run this once per session, under sudo, in its own shell --\n'
            f'     it re-adds the address after every reboot so the loop stays sudo-free:\n'
            f'     sudo {os.path.join(RIOTBASE, "dist/tools/suit/suit-ecm-keeper.sh")})')

    return problems


def detect_ecm_iface():
    matches = glob.glob('/sys/bus/usb/drivers/cdc_ether/*/net/*')
    return os.path.basename(matches[0]) if matches else None


def check_instrumented(con):
    """A build without SUIT_PERF=1 updates fine and measures nothing -- the
    single most wasteful way for a sweep to fail."""
    con.drain(0.5)
    con.send('')
    con.drain(0.3)
    cfg = read_config(con)
    if cfg is None:
        return ('the running image did not answer `suit_perf`.\n'
                '    Either it was flashed without SUIT_PERF=1, or the console\n'
                '    is not the RIOT shell. Reflash with SUIT_PERF=1 PROGRESS_BAR=0.')
    return None


# --------------------------------------------------------------------------


def aggregate(runs, tier_name, tier):
    """Per-phase distribution across the N runs, plus the memory indicators."""
    rows = []
    phases = []
    for r in runs:
        for p in r['records']:
            if p not in phases:
                phases.append(p)

    for phase in phases:
        samples = [r['records'][phase]['us'] for r in runs if phase in r['records']]
        sizes = {r['records'][phase]['bytes'] for r in runs if phase in r['records']}
        stats = summarise(samples)
        # A phase whose byte count moves between runs is not comparable as a
        # time; flag it rather than average over it.
        nbytes = sizes.pop() if len(sizes) == 1 else -1
        rows.append((phase, stats, nbytes, len(sizes) > 0))

    stack = [r['mem']['stack_used'] for r in runs if 'stack_used' in r['mem']]
    heap = [r['mem']['heap_hwm'] for r in runs if 'heap_hwm' in r['mem']]
    return rows, stack, heap


def main(args):
    if args.tier not in TIERS:
        raise SystemExit(f'unknown tier {args.tier}; pick one of {sorted(TIERS)}')
    tier = TIERS[args.tier]

    print(f'# device_perf_bench: tier={args.tier} sig={tier.sig} kem={tier.kem} '
          f'repeats={args.repeats} trigger={args.trigger}')

    problems = preflight(args, tier)
    if problems and not args.force:
        print('\npre-flight failed:\n', file=sys.stderr)
        for p in problems:
            print(f'  - {p}\n', file=sys.stderr)
        return 1
    for p in problems:
        print(f'# WARNING (forced past): {p.splitlines()[0]}')

    if args.repeats == 0:
        # Dry run: exercise the whole host half with no board attached, which
        # is how this harness gets reviewed before a capture session.
        print('# dry run (--repeats 0): publish + encrypt only, no device')
        publish(tier, int(time.time()), args.coap_server)
        encrypt_manifest(tier, args.coap_root)
        enc = os.path.join(args.coap_root, 'fw', 'suit_update',
                           'nrf52840dongle', 'riot.suit.enc')
        print(f'# OK: {enc} is {os.path.getsize(enc)} B')
        return 0

    con = Console(args.port, verbose=args.verbose)
    print(f'# console: {con.open()}')
    err = check_instrumented(con)
    if err and not args.force:
        print(f'\npre-flight failed:\n  - {err}\n', file=sys.stderr)
        return 1

    outfile = args.out or os.path.expanduser(
        f'~/suit-perf-logs/dev-{args.tier}-{args.trigger}.csv')
    os.makedirs(os.path.dirname(outfile), exist_ok=True)

    runs, failures = [], 0
    app_ver = int(time.time())
    with open(outfile, 'a') as raw_log:
        raw_log.write(f'# device_perf_bench {args.tier} {args.trigger} '
                      f'{time.strftime("%Y-%m-%dT%H:%M:%S")}\n')
        for i in range(1, args.repeats + 1):
            app_ver += 1
            try:
                r = run_once(con, tier, args, app_ver, i)
            except (RuntimeError, TimeoutError) as e:
                failures += 1
                print(f'  run {i}: FAILED -- {e}', file=sys.stderr)
                try:
                    con.reopen()
                except TimeoutError:
                    print('  console did not come back; stopping', file=sys.stderr)
                    break
                continue
            runs.append(r)
            if r['cfg']:
                raw_log.write(r['cfg'] + '\n')
            for line in r['raw']:
                raw_log.write(line + '\n')
            raw_log.write(f'# run {i} seqnr={r["seqnr"]} slot={r["slot"]} '
                          f'rebooted={r["rebooted"]}\n')
            raw_log.flush()
    con.close()

    if not runs:
        print('no successful runs', file=sys.stderr)
        return 1

    print(f'\n# {len(runs)} successful runs, {failures} failed')
    print(f'# raw PERF/PERFMEM lines: {outfile}')
    slots = [r['slot'] for r in runs]
    print(f'# slots visited: {slots}')
    cfgs = {r['cfg'] for r in runs if r['cfg']}
    for c in sorted(cfgs):
        print(f'# {c}')
    if len(cfgs) > 1:
        print('# WARNING: PERFCFG changed between runs -- not one configuration')

    rows, stack, heap = aggregate(runs, args.tier, tier)
    print()
    print(bench_header(BENCH_PREFIX))
    for phase, stats, nbytes, varied in rows:
        print(bench_line(BENCH_PREFIX, args.tier, tier.sig, tier.kem,
                         phase, stats, nbytes))
        if varied:
            print(f'# WARNING: {phase} byte count varied between runs')
    if stack:
        print(bench_line(BENCH_PREFIX, args.tier, tier.sig, tier.kem,
                         'stack_max_used', summarise(stack),
                         runs[0]['mem'].get('stack_size', 0)))
    if heap:
        print(bench_line(BENCH_PREFIX, args.tier, tier.sig, tier.kem,
                         'heap_hwm', summarise(heap), 0))

    print()
    print(format_header())
    for phase, stats, nbytes, _ in rows:
        print(format_row(args.tier, phase, tier.sig, tier.kem, stats, nbytes))
    return 0


def parse_arguments():
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=__doc__.split('\n\n')[0])
    p.add_argument('--tier', required=True, choices=sorted(TIERS),
                   help='which crypto tier the board is currently flashed with')
    p.add_argument('--repeats', type=int, default=20,
                   help='number of updates to run; 0 = dry run, no device')
    p.add_argument('--trigger', choices=['fetch', 'notify'], default='fetch',
                   help='`suit fetch` on the device shell (avoids the notifier '
                        'retransmitting during the payload transfer), or the '
                        'CoAP notify the manual runbook uses')
    p.add_argument('--port', default='auto',
                   help='console device, or "auto" to pick the ttyACM node')
    p.add_argument('--coap-server', default='[2001:db8::1]')
    p.add_argument('--client', default=None,
                   help='SUIT_CLIENT for --trigger notify, e.g. [fe80::2%%eth1]')
    p.add_argument('--coap-root', default=os.path.join(RIOTBASE, 'coaproot'))
    p.add_argument('--out', default=None,
                   help='raw PERF/PERFMEM log (default ~/suit-perf-logs/dev-<tier>-<trigger>.csv)')
    p.add_argument('--host-perf-log', default=None,
                   help='also capture the producer side per iteration, giving '
                        'paired host+device samples (see PERF_RESULTS_HOST.md)')
    p.add_argument('--timeout', type=float, default=120,
                   help='seconds to wait for one update to report')
    p.add_argument('--reboot-timeout', type=float, default=30)
    p.add_argument('--link-timeout', type=float, default=45,
                   help='seconds to wait after a reboot for the node to be '
                        'able to ping the CoAP server before triggering')
    p.add_argument('--verbose', action='store_true',
                   help='echo the device console')
    p.add_argument('--force', action='store_true',
                   help='continue past pre-flight failures')
    return p.parse_args()


if __name__ == '__main__':
    sys.exit(main(parse_arguments()))
