#!/bin/bash
# Runs one scenario cycle, unattended:
#   prep (before the cool-down), cool-down, pre-checks, clean baseline,
#   fault on (verified), lead time, preflight and agents (fault still on),
#   the collector's delivery check over the agents' window, fault off
#   (verified).
#
#   agents/run-scenario.sh "<label>|<kind>|<fault>|<variant>|<lead min>|<restart>"
#
#   kind     flag:   <fault> is a flagd flag, switched off to <variant> and back
#                    with agents/ops.sh flip; <restart> (service names) is
#                    restarted before the cool-down, for a fresh flagd
#                    connection, and again after the fault is off.
#            custom: <fault> is a script in agents/custom/ taking
#                    prep|on|off|status; <restart> is "-" or services to
#                    restart after the fault is off.
#
# Settings (environment): COOLDOWN_MIN (8), BASELINE_MIN (10),
# AGENT_TIMEOUT_MIN (70), QUESTIONS (agents/questions.md), REPS (5),
# MAXPAR (15), STAGGER_S (20).
#
# Keep the host awake for the whole run (on macOS: caffeinate -dimsu).
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
O=$R/agents/ops.sh
CUSTOM=$R/agents/custom
SHOP="${SHOP_DIR:-$R/otel-demo}"
COOLDOWN_MIN=${COOLDOWN_MIN:-8}
BASELINE_MIN=${BASELINE_MIN:-10}
AGENT_TIMEOUT_MIN=${AGENT_TIMEOUT_MIN:-70}
export REPS=${REPS:-5} MAXPAR=${MAXPAR:-15} STAGGER_S=${STAGGER_S:-20}
WATCH="cart valkey-cart product-catalog astronomy-db accounting checkout frontend kafka ad recommendation fraud-detection"

[[ $# -eq 1 ]] || { echo "usage: $0 \"<label>|<flag|custom>|<fault>|<variant>|<lead min>|<restart>\"" >&2; exit 2; }
IFS='|' read -r label kind fault variant lead restart <<<"$1"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
mins() { sleep $(( $1 * 60 )); }
utc() { python3 -c 'import sys,time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(sys.argv[1]))))' "$1"; }
killtree() { for c in $(pgrep -P "$1"); do killtree "$c"; done; kill "$1" 2>/dev/null; }

containers() {  # name restarts started status mem
  for c in $WATCH; do
    s=$(docker inspect -f '{{.RestartCount}} {{.State.StartedAt}} {{.State.Status}}' "$c" 2>/dev/null)
    m=$(docker stats --no-stream --format '{{.MemUsage}}' "$c" 2>/dev/null)
    log "  $c restarts/started/status: $s mem: $m"
  done
}

all_flags_off() {
  python3 - "$SHOP/src/flagd/demo.flagd.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))['flags']
on = {k: v['defaultVariant'] for k, v in d.items() if v['defaultVariant'] != 'off'}
print("flags not off:", on or "none"); sys.exit(1 if on else 0)
PY
}

kafka_ok() {
  out=$($O kafka-check 2>&1); rc=$?; log "  kafka: $(echo $out)"
  if [[ $rc -ne 0 ]]; then
    for g in accounting fraud-detection; do
      if echo "$out" | grep -q "^$g .*members=0"; then
        log "  $g has no active members: resetting"; $O kafka-reset "$g" 2>&1 | grep -E "RESET|Error" | sed 's/^/  /'
      fi
    done
    return 1
  fi
  return 0
}

preflight_platforms() {  # prints the platforms whose login is OK
  local pf ok=()
  for attempt in 1 2 3; do
    pf=$($R/agents/preflight.sh 2>&1)
    echo "$pf" | sed 's/^/  preflight: /' >&2
    echo "$pf" | grep -q '^FAIL' || break
    sleep 60
  done
  for p in coralogix datadog dynatrace grafana newrelic; do
    echo "$pf" | grep -q "^OK   $p" && ok+=("$p")
  done
  echo "${ok[@]}"
}

fault_on() {
  if [[ $kind == flag ]]; then $O flip "$fault" off "$variant"; else $CUSTOM/$fault on; fi
}
fault_off() {
  if [[ $kind == flag ]]; then $O flip "$fault" "$variant" off; else $CUSTOM/$fault off; fi
}

log "=== $label ($kind $fault): cool-down $COOLDOWN_MIN min before baseline"
# quote (PHP) drifts its span and metric time base across host sleeps until
# platforms reject its data as more than 24 h old. Restart it at the start of
# the cool-down, outside the window.
$O dc restart quote 2>&1 | sed 's/^/  /'; log "  restarted quote (clock-drift guard)"
if [[ $kind == flag ]]; then
  # A long-running service can stop picking up flagd changes: restart the
  # fault's own services here, outside the window.
  $O dc restart $restart 2>&1 | sed 's/^/  /'; log "  restarted $restart (fresh flagd connection)"
else
  $CUSTOM/$fault prep 2>&1 | sed 's/^/  /'
fi
mins "$COOLDOWN_MIN"

log "--- $label pre-checks"
if ! all_flags_off; then log "ABORT: a flag is still on"; exit 1; fi
if ! kafka_ok; then log "  Kafka consumer reset; waiting 15 min so it falls before the baseline"; mins 15; kafka_ok; fi
containers

base=$(date -u +%s); log "--- $label BASELINE start"
mins "$BASELINE_MIN"

out=$(fault_on 2>&1); echo "$out" | sed 's/^/  /'
if ! echo "$out" | grep -q VERIFIED; then
  log "SKIP $label: fault not verified; switching it off"
  fault_off 2>&1 | sed 's/^/  /'
  exit 1
fi
log "--- $label FAULT ON; lead time $lead min"
mins $(( lead / 2 )); log "  midpoint snapshot"; containers
mins $(( lead - lead / 2 ))

# Fixed UTC times: a relative "last N minutes" would drift between questions.
w0=$(utc $(( base - 120 ))); w1=$(utc "$(date -u +%s)")
window="the period from $w0 to $w1 (UTC)"
containers; kafka_ok || true
plats=$(preflight_platforms)
log "--- $label AGENTS start: WINDOW=$window platforms=[$plats]"
if [[ -n "$plats" ]]; then
  # A watchdog (portable to hosts without `timeout`) stops the agents after AGENT_TIMEOUT_MIN.
  WINDOW="$window" $R/agents/run-agents-rep.sh "$label" $plats > >(sed 's/^/  /') 2>&1 &
  apid=$!; waited=0
  while kill -0 $apid 2>/dev/null && (( waited < AGENT_TIMEOUT_MIN * 60 )); do sleep 15; waited=$(( waited + 15 )); done
  if kill -0 $apid 2>/dev/null; then
    log "  agents still running after $AGENT_TIMEOUT_MIN min: stopping them"
    killtree $apid
  fi
  wait $apid 2>/dev/null
  log "--- $label AGENTS finished"
else
  log "  no platform passed preflight; agents not run"
fi

# The collector must have delivered everything each platform's pipeline
# keeps in the agents' window: no refused data, no queue overflow, no failed
# sends, no trace decided before it was complete. Drops come only from the
# sampling and cuts. A failure marks the run for a retry.
if ! $R/agents/delivery-check.py "$w0" "$w1" 2>&1 | sed 's/^/  /'; then
  log "DELIVERY FAILED in $label's window"
fi

fault_off 2>&1 | sed 's/^/  /'
log "--- $label FAULT OFF"
containers
[[ "$restart" != "-" ]] && { $O dc restart $restart 2>&1 | sed 's/^/  /'; log "  restarted $restart"; }
log "=== $label done"
