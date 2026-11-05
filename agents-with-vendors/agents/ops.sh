#!/bin/bash
# Stack helpers. Paths are absolute, resolved from this file's location.
#
#   agents/ops.sh dc <compose args...>      docker compose with every env and compose file
#   agents/ops.sh flip <flag> <from> <to>   edit flagd's file (same inode), then confirm over OFREP (exit 1 if not served)
#   agents/ops.sh served <flag>             the variant flagd serves
#   agents/ops.sh kafka-check               lag and active members for accounting and fraud-detection
#   agents/ops.sh kafka-reset <group>       stop the consumer, reset its group to latest, start it (throwaway admin container)
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
D="${SHOP_DIR:-$R/otel-demo}"
F=$D/src/flagd/demo.flagd.json

dc() {
  docker compose --project-directory "$D" --env-file "$D/.env" --env-file "$D/.env.override" --env-file "$R/.env" \
    -f "$D/compose.yaml" -f "$D/compose.full.yaml" -f "$D/compose.observability.yaml" -f "$D/compose.extras.yaml" \
    -f "$D/compose.ai.yaml" -f "$D/compose.search.yaml" "$@"
}

served() {
  dc exec -T load-generator python3 -c "import urllib.request,json; r=urllib.request.Request('http://flagd:8016/ofrep/v1/evaluate/flags/$1', data=json.dumps({'context':{}}).encode(), headers={'Content-Type':'application/json'}); print(json.loads(urllib.request.urlopen(r,timeout=5).read())['variant'])"
}

flip() {
  local flag=$1 from=$2 to=$3
  python3 - "$F" "$flag" "$from" "$to" <<'PY' || return 1
import sys; p, flag, a, b = sys.argv[1:]; s = open(p).read()
old = f'"{flag}": {{\n      "defaultVariant": "{a}",'
assert old in s, f"{flag} is not at {a}"
s = s.replace(old, f'"{flag}": {{\n      "defaultVariant": "{b}",')
with open(p, 'r+') as f: f.seek(0); f.write(s); f.truncate()
PY
  echo "FLIPPED $flag $from to $to at $(date -u +%H:%M:%SZ)"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 3
    v=$(served "$flag" 2>&1)
    if [[ "$v" == "$to" ]]; then echo "VERIFIED $flag=$to at $(date -u +%H:%M:%SZ)"; return 0; fi
  done
  echo "NOT VERIFIED $flag (served: $v)"; return 1
}

# The Kafka image is tagged harness-kafka-admin:1 (scripts/setup-shop.sh), so
# the collector hides these containers from docker_stats.
kafka_admin() {
  docker run --rm --network opentelemetry-demo --entrypoint /opt/kafka/bin/kafka-consumer-groups.sh \
    -e JAVA_TOOL_OPTIONS= harness-kafka-admin:1 --bootstrap-server kafka:9092 "$@"
}

# Prints "<group> lag=<n> members=<n>" per group; exit 1 if a group has no members.
kafka_check() {
  local rc=0
  for g in accounting fraud-detection; do
    out=$(kafka_admin --describe --group "$g" 2>&1)
    lag=$(echo "$out" | awk '$1=="'$g'" && $6 ~ /^[0-9]+$/ {s+=$6} END {print s+0}')
    members=$(echo "$out" | awk '$1=="'$g'" && $7 != "-" && $7 != "" {print $7}' | sort -u | wc -l | tr -d ' ')
    echo "$g lag=$lag members=$members"
    [[ "$members" -eq 0 ]] && rc=1
  done
  return $rc
}

kafka_reset() {
  local g=$1
  dc stop "$g"
  # Right after the stop the group stays "Stable" until the old member's
  # session times out, and the reset is refused; retry for up to about 3 min.
  local out
  for _ in $(seq 1 18); do
    out=$(kafka_admin --group "$g" --reset-offsets --to-latest --all-topics --execute 2>&1)
    echo "$out" | grep -q "current state is" || break
    sleep 10
  done
  echo "$out"
  dc start "$g"
  echo "RESET $g at $(date -u +%H:%M:%SZ)"
}

cmd=$1; shift
case $cmd in
  dc) dc "$@" ;;
  flip) flip "$@" ;;
  served) served "$@" ;;
  kafka-check) kafka_check ;;
  kafka-reset) kafka_reset "$@" ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
