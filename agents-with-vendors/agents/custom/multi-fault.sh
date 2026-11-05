#!/bin/bash
# Scenario "five faults" (open exploration). Services and signals chosen not
# to mask each other:
#   paymentFailure 25%          payment         errors, gold-tier segment
#   adManualGc on               ad              JVM / GC latency
#   recommendationCacheFailure  recommendation  memory leak, crash loop
#   latency bursts              cart to valkey  periodic latency
#   emailMemoryLeak 100x        email           slow memory growth
#
#   multi-fault.sh prep | on | off | status
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
O=$R/agents/ops.sh
B=$R/agents/custom/latency-bursts.sh
FLAGS=("paymentFailure|25%" "adManualGc|on" "recommendationCacheFailure|on" "emailMemoryLeak|100x")
ts() { date -u +%H:%M:%SZ; }
case "${1:-}" in
  prep)
    # Fresh flagd connections for the four flag services (a long-running
    # service can ignore a flag change). prep runs at cool-down start.
    $O dc restart payment ad recommendation email 2>&1 | tail -1
    $B prep ;;
  on)
    ok=1
    for f in "${FLAGS[@]}"; do IFS='|' read -r fl v <<<"$f"; $O flip "$fl" off "$v" | grep -q VERIFIED && echo "  $fl=$v" || { echo "  $fl NOT VERIFIED"; ok=0; }; done
    $B on | grep -q VERIFIED && echo "  latency bursts on" || { echo "  bursts NOT VERIFIED"; ok=0; }
    (( ok )) && echo "VERIFIED five faults on at $(ts)" || { echo "NOT VERIFIED (some faults failed)"; exit 1; } ;;
  off)
    for f in "${FLAGS[@]}"; do IFS='|' read -r fl v <<<"$f"; [[ "$($O served "$fl" 2>/dev/null)" != off ]] && $O flip "$fl" "$v" off | tail -1; done
    $B off | tail -1
    $O dc restart ad recommendation email 2>&1 | tail -1
    echo "VERIFIED five faults off; ad, recommendation, email restarted at $(ts)" ;;
  status) for f in "${FLAGS[@]}"; do IFS='|' read -r fl v <<<"$f"; echo "$fl=$($O served "$fl")"; done; $B status ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
