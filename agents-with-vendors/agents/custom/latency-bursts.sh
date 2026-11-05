#!/bin/bash
set -uo pipefail
DELAY_MS=${DELAY_MS:-250}
ON_S=${ON_S:-60}
PERIOD_S=${PERIOD_S:-300}
H=${HELPER:-harness-net-valkey}
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$R/results"
PIDFILE=$R/results/.latency-bursts.pid
LOOPLOG=${LOOPLOG:-$R/results/latency-bursts-loop.log}

ts() { date -u +%H:%M:%SZ; }
qdisc() { docker exec $H tc qdisc show dev eth0 2>/dev/null | head -1; }
delay_on() { docker exec $H tc qdisc replace dev eth0 root netem delay ${DELAY_MS}ms; }
delay_off() { docker exec $H tc qdisc del dev eth0 root 2>/dev/null || true; }

case "${1:-}" in
  prep)
    docker rm -f $H >/dev/null 2>&1
    docker run -d --rm --name $H --net container:${TARGET:-valkey-cart} --cap-add NET_ADMIN harness-nettools:1 >/dev/null \
      && echo "PREP helper up at $(ts)" || { echo "PREP FAILED"; exit 1; } ;;
  on)
    docker inspect $H >/dev/null 2>&1 || { echo "helper not running; run prep"; exit 1; }
    (
      trap 'delay_off; echo "BURST loop stopped at $(ts)"; exit 0' TERM
      while true; do
        delay_on && echo "BURST on ${DELAY_MS}ms at $(ts)"
        sleep "$ON_S" & wait $!
        delay_off; echo "BURST off at $(ts)"
        sleep $(( PERIOD_S - ON_S )) & wait $!
      done
    ) >> "$LOOPLOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 3
    if qdisc | grep -q "netem.*delay"; then echo "VERIFIED bursts running (pid $(cat $PIDFILE)): $(qdisc)"; else echo "NOT VERIFIED: $(qdisc)"; exit 1; fi ;;
  off)
    [[ -f "$PIDFILE" ]] && { kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; }
    sleep 1; delay_off
    q=$(qdisc)
    docker rm -f $H >/dev/null 2>&1
    if echo "$q" | grep -q netem; then echo "NOT VERIFIED off: $q"; exit 1; fi
    echo "VERIFIED bursts off, helper removed at $(ts)" ;;
  status)
    echo "qdisc: $(qdisc)"; [[ -f "$PIDFILE" ]] && echo "loop pid $(cat "$PIDFILE")" || echo "no loop" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
