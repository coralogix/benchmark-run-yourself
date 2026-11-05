#!/bin/bash
# Scenario "Kafka queue flood" (demo flag kafkaQueueProblems), as a fault
# script so the Kafka-specific guards and the backlog clean-up live in one
# place. The fault itself is the demo flag: checkout
# sends each order to `orders` 1 + 100 times (the extra copies are untraced),
# and fraud-detection sleeps 1 s per record, so its lag climbs steadily.
# Both fault-announcement log lines are dropped for every platform
# (filter/drop_fault_announcement_logs).
#
#   kafka-flood.sh prep    guards: flag off, both groups active and lag < 50,
#                          Kafka anon memory < 610 MiB, fraud-detection's
#                          rule-cache mode file "normal"; records Kafka's start time
#   kafka-flood.sh on      flip kafkaQueueProblems off to on (verified over OFREP)
#   kafka-flood.sh off     flip on to off; reset fraud-detection (and accounting
#                          if its lag > 500) to latest; restart checkout if
#                          Kafka restarted during the fault or the proxy shows
#                          504s; final kafka-check
#   kafka-flood.sh status  flag, lag, Kafka memory, 504s in the last 5 min
#
# Everything in `off` runs after the agents finish, so outside the window.
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
O=$R/agents/ops.sh
mkdir -p "$R/results"
STATE=$R/results/.kafka-flood.started
ts() { date -u +%H:%M:%SZ; }
kstart() { docker inspect -f '{{.State.StartedAt}}' kafka; }
kanon_mib() { docker exec kafka cat /sys/fs/cgroup/memory.stat 2>/dev/null | awk '$1=="anon"{printf "%d", $2/1048576}'; }
n504() { docker logs --since "${1:-5m}" frontend-proxy 2>&1 | grep -c '" 504 '; }
lag_of() { echo "$1" | awk -v g="$2" '$1==g {sub("lag=","",$2); print $2}'; }

case "${1:-}" in
  prep)
    ok=1
    v=$($O served kafkaQueueProblems 2>&1); [[ "$v" == off ]] || { echo "PREP: kafkaQueueProblems served '$v'"; ok=0; }
    kc=$($O kafka-check 2>&1); echo "PREP kafka: $(echo $kc)"
    for g in accounting fraud-detection; do
      l=$(lag_of "$kc" "$g"); [[ -n "$l" && "$l" -lt 50 ]] || { echo "PREP: $g lag '$l', resetting"; $O kafka-reset "$g" 2>&1 | grep -E "RESET|Error"; }
    done
    a=$(kanon_mib); echo "PREP kafka anon ${a} MiB (heap fixed at 400m; limit 620 MiB)"
    [[ -n "$a" && "$a" -lt 610 ]] || { echo "PREP: Kafka anon memory high (${a} MiB)"; ok=0; }
    kstart > "$STATE"
    (( ok )) && echo "PREP ok at $(ts)" || { echo "PREP FAILED at $(ts)"; exit 1; } ;;
  on)
    $O flip kafkaQueueProblems off on ;;
  off)
    out=$($O flip kafkaQueueProblems on off 2>&1); echo "$out"
    echo "$out" | grep -q VERIFIED || exit 1
    sleep 5
    $O kafka-reset fraud-detection 2>&1 | grep -E "RESET|Error" | head -2
    kc=$($O kafka-check 2>&1)
    l=$(lag_of "$kc" accounting)
    if [[ -z "$l" || "$l" -gt 500 ]]; then $O kafka-reset accounting 2>&1 | grep -E "RESET|Error" | head -2; fi
    if [[ -s "$STATE" && "$(kstart)" != "$(cat "$STATE")" ]]; then
      echo "Kafka restarted during the fault ($(cat "$STATE") to $(kstart)): restarting checkout"
      docker restart checkout >/dev/null
    elif (( $(n504 5m) > 20 )); then
      echo "$(n504 5m) proxy 504s in 5 min: restarting checkout"; docker restart checkout >/dev/null
    fi
    sleep 30
    echo "after clean-up: $($O kafka-check 2>&1 | tr '\n' ' ')"
    echo "VERIFIED kafkaQueueProblems off, backlog reset at $(ts)" ;;
  status)
    echo "flag: $($O served kafkaQueueProblems 2>&1)"
    $O kafka-check 2>&1
    echo "kafka anon $(kanon_mib) MiB, started $(kstart); 504s last 5 min: $(n504 5m)" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
