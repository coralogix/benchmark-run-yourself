#!/bin/bash
# Scenario "CPU throttle": a too-tight CPU limit on product-catalog
# ("someone set the limits too tight"). No flagd flag, no code change.
#
#   cpu-throttle.sh prep    no-op (nothing to stage)
#   cpu-throttle.sh on      set the limit, exit 0 once verified
#   cpu-throttle.sh off     recreate the container so the limit is exactly as before (NanoCpus 0)
#   cpu-throttle.sh status  print the current limit
#
# At 10 users, 0.1 CPU takes the product API p50 from about 30-50 ms to
# about 150 ms with no errors. `docker update --cpus 0` does not remove a
# limit, so `off` recreates the container (a restart event, after the agents
# finish).
set -uo pipefail
SVC=${SVC:-product-catalog}
CPUS=${CPUS:-0.1}
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
O=$R/agents/ops.sh
ts() { date -u +%H:%M:%SZ; }
nano() { docker inspect -f '{{.HostConfig.NanoCpus}}' "$SVC"; }

case "${1:-}" in
  prep) echo "PREP nothing to stage ($SVC limit now $(nano))" ;;
  on)
    docker update --cpus "$CPUS" "$SVC" >/dev/null
    want=$(python3 -c "print(int(float('$CPUS')*1e9))")
    if [[ "$(nano)" == "$want" ]]; then echo "VERIFIED $SVC cpus=$CPUS at $(ts)"; else echo "NOT VERIFIED: NanoCpus=$(nano)"; exit 1; fi ;;
  off)
    $O dc up -d --no-deps --force-recreate "$SVC" 2>&1 | tail -1
    if [[ "$(nano)" == "0" ]]; then echo "VERIFIED $SVC limit removed (recreated) at $(ts)"; else echo "NOT VERIFIED: NanoCpus=$(nano)"; exit 1; fi ;;
  status) echo "$SVC NanoCpus=$(nano)" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
