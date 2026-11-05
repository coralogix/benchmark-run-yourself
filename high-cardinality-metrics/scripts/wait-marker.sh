#!/bin/bash
# wait-marker.sh <marker> [<marker2> ...]: block until every marker exists in
# s3://$BENCH_BUCKET/$RUN_TAG/, or until any ERROR-* appears. Exits 0 on success,
# 2 on error, 1 on timeout (WAIT_MAX seconds, default 7200).
#   scripts/wait-marker.sh 05-all-sweeps-done
set -uo pipefail
B="${BENCH_BUCKET:?set BENCH_BUCKET}"
P="${RUN_TAG:-hcm-bench}"
export AWS_REGION="${AWS_REGION:?set AWS_REGION}"
DEADLINE=$(( $(date -u +%s) + ${WAIT_MAX:-7200} ))
while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  LS=$(aws s3 ls "s3://$B/$P/" 2>/dev/null | awk '{print $4}')
  if echo "$LS" | grep -q '^ERROR-'; then
    E=$(echo "$LS" | grep '^ERROR-' | head -1)
    echo "!!! $E"
    aws s3 cp "s3://$B/$P/$E" - 2>/dev/null | head -5
    exit 2
  fi
  ALL=1
  for m in "$@"; do echo "$LS" | grep -qx "$m" || ALL=0; done
  if [ "$ALL" = 1 ]; then
    echo "REACHED: $*"
    for m in "$@"; do printf "  %s : %s\n" "$m" "$(aws s3 cp "s3://$B/$P/$m" - 2>/dev/null | head -1)"; done
    echo "--- markers so far ---"; echo "$LS" | grep -E '^(0[0-9]|ERROR)' | sort
    exit 0
  fi
  sleep 20
done
echo "TIMEOUT waiting for: $*"
echo "$LS" | grep -E '^(0[0-9]|ERROR)' | sort
exit 1
