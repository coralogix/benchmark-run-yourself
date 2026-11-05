#!/bin/bash
set -uo pipefail
PCT=${FT4_PCT:-25}
C=fraud-detection
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
O=$R/agents/ops.sh
source "$R/agents/env.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ts() { date -u +%H:%M:%SZ; }
write_conf() { printf '%s\n' "$1" > "$TMP/order-review.conf" && docker cp "$TMP/order-review.conf" $C:/tmp/order-review.conf; }
read_conf() { docker cp $C:/tmp/order-review.conf - 2>/dev/null | tar -xO 2>/dev/null; }
threads() {
  cx --profile "${CX_PROFILE:-competitor-comparison}" metrics query \
    'sum by (jvm_thread_daemon, jvm_thread_state) (jvm_thread_count__thread_{service_name="fraud-detection"})' 2>&1 | grep -E '\|' ;
}

case "${1:-}" in
  prep)
    $O dc restart $C >/dev/null 2>&1 || { echo "PREP FAILED: restart"; exit 1; }
    for _ in $(seq 1 12); do docker inspect -f '{{.State.Running}}' $C 2>/dev/null | grep -q true && break; sleep 5; done
    write_conf sync && [[ "$(read_conf)" == "sync" ]] && echo "PREP restarted $C, conf=sync at $(ts)" || { echo "PREP FAILED: conf"; exit 1; } ;;
  on)
    write_conf "async $PCT" || { echo "NOT VERIFIED: write failed"; exit 1; }
    [[ "$(read_conf)" == "async $PCT" ]] && echo "VERIFIED conf=async ${PCT}% at $(ts)" \
      || { echo "NOT VERIFIED: conf reads '$(read_conf)'"; exit 1; } ;;
  off)
    write_conf sync
    [[ "$(read_conf)" == "sync" ]] && echo "VERIFIED conf=sync at $(ts) (reviewer threads shut down on the next order)" \
      || { echo "NOT VERIFIED off"; exit 1; } ;;
  status)
    echo "conf: $(read_conf)"
    docker stats --no-stream --format 'mem {{.MemUsage}} pids {{.PIDs}}' $C
    threads ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
