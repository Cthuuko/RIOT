#!/usr/bin/env bash
# Keep the host end of the CDC-ECM link alive across nRF52840 Dongle reboots.
#
# A SUIT update reboots the dongle, which re-enumerates its composite USB
# device. The cdc_ether interface is destroyed and recreated, which loses
# *everything* the host had configured on it -- not only the address, but the
# per-interface sysctls, the link-local, the route, and the uhcpd binding.
#
# fe80::1 is the one that matters and the one that is easy to miss. The node
# never gets a global address: gnrc_uhcpc.c:100 starts the uhcp client only on
# a node with two interfaces, and the dongle has one. What it does get, from
# set_interface_roles(), is fe80::2 and a default route via fe80::1 -- so the
# fetch goes out from a link-local source through that next hop. Miss fe80::1
# on the host and the node has a route to nowhere: every fetch dies in
# retransmissions while still printing a complete, all-zero perf report that
# looks like a successful run. Measured: mfst_fetch of 90.8 s and 79.1 s in
# ~/suit-perf-logs, against 16.7 ms on a healthy link.
#
# uhcpd is started for parity with start_network.sh and is vestigial on this
# board -- with one interface the node never sends a UHCP request, so the
# daemon simply never hears from it. It is kept for the two-interface
# (border-router) configurations the same prefix delegation does serve.
#
# This does for the whole session what `make term` (dist/tools/usb-cdc-ecm/
# start_network.sh, the source of truth for these primitives) does once. Run it
# ONCE, under sudo, in its own terminal:
#
#     sudo dist/tools/suit/suit-ecm-keeper.sh
#
# It takes over from any instance already running, so re-running it is the
# normal way to restart it. To stop every instance and leave nothing behind:
#
#     sudo dist/tools/suit/suit-ecm-keeper.sh --stop
#
# The measurement loop itself never needs root. Ctrl-C to stop.
#
# This is deliberately a separate script rather than something the harness
# shells out to: the harness runs unprivileged and never invokes sudo.

set -u

STOP_ONLY=0
if [ "${1:-}" = "--stop" ] || [ "${1:-}" = "-s" ]; then
    STOP_ONLY=1
    shift
fi

ADDR="${1:-2001:db8::1/64}"
PREFIX="${2:-2001:db8::/64}"
INTERVAL="${3:-2}"

SELF="$(readlink -f "$0")"
RIOTBASE="$(cd "$(dirname "$SELF")/../../.." && pwd)"
UHCPD="$RIOTBASE/dist/tools/uhcpd/bin/uhcpd"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: needs root -- run: sudo $0 $*" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# taking over from previous instances

# Our own pid and every ancestor, so we never signal the shell (or the sudo
# wrappers) we are running under.
ancestors() {
    local pid=$$
    while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
        echo "$pid"
        pid=$(awk '/^PPid:/{print $2}' "/proc/$pid/status" 2>/dev/null)
    done
}

# Computed once, before any subshell exists that could confuse the scan.
SKIP=" $(ancestors | tr '\n' ' ') "

# Our own subshells inherit our cmdline, so a name match alone would list
# `$(other_instances)` itself -- a pid that is already gone by the time anything
# signals it, and so could name an unrelated process by then. Both directions of
# the tree have to be excluded, not just upwards.
is_ours() {
    local pid=$1 hops=0
    case "$SKIP" in *" $pid "*) return 0 ;; esac
    while [ -n "$pid" ] && [ "$pid" -gt 1 ] && [ "$hops" -lt 32 ]; do
        [ "$pid" = "$$" ] && return 0
        pid=$(awk '/^PPid:/{print $2}' "/proc/$pid/status" 2>/dev/null)
        hops=$((hops + 1))
    done
    return 1
}

other_instances() {
    local pid cmd
    for c in /proc/[0-9]*/cmdline; do
        pid=${c#/proc/}
        pid=${pid%/cmdline}
        cmd=$({ tr '\0' ' ' < "$c"; } 2>/dev/null) || continue
        case "$cmd" in *suit-ecm-keeper*) ;; *) continue ;; esac
        is_ours "$pid" && continue
        echo "$pid"
    done
}

# An instance stopped with the old buggy cleanup ignores SIGTERM, so TERM alone
# is not enough to be sure -- escalate rather than leave a rival running.
kill_others() {
    local pids p
    pids=$(other_instances)
    [ -n "$pids" ] || return 0
    echo "suit-ecm-keeper: taking over from instance(s): $(echo $pids)"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    sleep 1
    for p in $pids; do
        if kill -0 "$p" 2>/dev/null; then
            kill -9 "$p" 2>/dev/null
            echo "suit-ecm-keeper: pid $p ignored SIGTERM, killed"
        fi
    done
}

# Two uhcpd on one link is worse than none: `make term` leaves one behind, and
# so does an instance killed before its cleanup ran.
kill_stray_uhcpd() {
    if pkill -f "$UHCPD" 2>/dev/null; then
        echo "suit-ecm-keeper: stopped stray uhcpd"
        sleep 0.3
    fi
}

if [ "$STOP_ONLY" -eq 1 ]; then
    kill_others
    kill_stray_uhcpd
    echo "suit-ecm-keeper: stopped"
    exit 0
fi

if [ ! -x "$UHCPD" ]; then
    echo "error: $UHCPD is missing -- build it with:" >&2
    echo "    make -C $RIOTBASE/dist/tools/uhcpd" >&2
    exit 1
fi

kill_others
kill_stray_uhcpd

# ---------------------------------------------------------------------------
# the link

uhcpd_pid=""

stop_uhcpd() {
    [ -n "$uhcpd_pid" ] || return 0
    kill "$uhcpd_pid" 2>/dev/null
    local i
    for i in 1 2 3 4 5; do
        kill -0 "$uhcpd_pid" 2>/dev/null || break
        sleep 0.2
    done
    kill -9 "$uhcpd_pid" 2>/dev/null
    wait "$uhcpd_pid" 2>/dev/null
    uhcpd_pid=""
}

start_uhcpd() {
    "$UHCPD" "$1" "$PREFIX" >/dev/null 2>&1 &
    uhcpd_pid=$!
    echo "suit-ecm-keeper: uhcpd serving $PREFIX on $1 (pid $uhcpd_pid)"
}

# The four things start_network.sh's setup_interface does, minus the parts
# that are its own teardown's business. `ip address add` exits 2 on EEXIST,
# which is the normal case on a tick where nothing changed.
setup_link() {
    local iface=$1
    sysctl -q -w net.ipv6.conf."$iface".forwarding=1
    sysctl -q -w net.ipv6.conf."$iface".accept_ra=0
    ip link set "$iface" up
    ip address add fe80::1/64 dev "$iface" 2>/dev/null
    ip address add "$ADDR" dev "$iface" 2>/dev/null
    # Where the node forwards a downstream prefix; harmless on a bare link,
    # and less preferred than the connected route, so it cannot break us.
    ip route replace "$PREFIX" via fe80::2 dev "$iface" 2>/dev/null
}

# Must exit: a signal trap that only returns drops back into the main loop,
# and with the traps disarmed on the way in there is then no way to stop this
# at all -- not even SIGTERM.
cleanup() {
    trap '' INT TERM EXIT
    echo
    echo "suit-ecm-keeper: stopping"
    stop_uhcpd
    exit 0
}
trap cleanup INT TERM EXIT

echo "suit-ecm-keeper: holding $ADDR and $PREFIX on the CDC-ECM interface (Ctrl-C to stop)"

last_ifindex=""
while true; do
    iface=""
    for net in /sys/bus/usb/drivers/cdc_ether/*/net/*; do
        [ -e "$net" ] || continue
        iface=$(basename "$net")
        break
    done

    if [ -n "$iface" ]; then
        # ifindex, not name: the name is derived from the MAC and so comes
        # back identical after a re-enumeration that invalidated everything.
        ifindex=$(cat "/sys/class/net/$iface/ifindex" 2>/dev/null || echo "")

        # Re-applied every tick rather than only on change: every command in
        # setup_link is idempotent and cheap, and the link can be torn down
        # without the ifindex moving -- `make term`'s start_network.sh deletes
        # fe80::1 and the route from under us when its shell exits.
        if ! ip -6 addr show dev "$iface" | grep -qw "${ADDR%%/*}"; then
            echo "suit-ecm-keeper: restoring $ADDR on $iface"
        fi
        setup_link "$iface"

        # uhcpd, by contrast, must not be churned: restart it only when the
        # netdev it bound to is genuinely gone, or when it is not running.
        if [ -n "$ifindex" ] && [ "$ifindex" != "$last_ifindex" ]; then
            echo "suit-ecm-keeper: $iface is new (ifindex $ifindex) -- reconfigured"
            stop_uhcpd
            start_uhcpd "$iface"
            last_ifindex="$ifindex"
        elif [ -z "$uhcpd_pid" ] || ! kill -0 "$uhcpd_pid" 2>/dev/null; then
            echo "suit-ecm-keeper: uhcpd is not running -- starting it"
            uhcpd_pid=""
            start_uhcpd "$iface"
        fi
    elif [ -n "$last_ifindex" ]; then
        echo "suit-ecm-keeper: interface gone, waiting for re-enumeration"
        stop_uhcpd
        last_ifindex=""
    fi

    sleep "$INTERVAL"
done
