#!/bin/bash
# Runs the ten scenarios one after another: one fault cycle each, five agents
# per platform (25 per window), at most MAXPAR=15 running at once.
#
#   agents/run-all.sh                 all ten, logging to results/run-all.log
#   FROM=3 TO=5 agents/run-all.sh     entries 3 to 5 only (1-based)
#   DRY=1 agents/run-all.sh           print the plan only
#
# A run that fails mechanically (a non-zero exit, a fault that never
# verified, a platform failing preflight, agents stopped by the timeout, or
# the collector failing to deliver part of what a pipeline keeps)
# is retried once at the end under the label suffix R, for every platform.
# Keep the host awake for the whole run (on macOS: caffeinate -dimsu).
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A=$R/agents
source "$A/env.sh"
export REPS=5 MAXPAR=15 STAGGER_S=20 COOLDOWN_MIN=8 BASELINE_MIN=10
export AGENT_TIMEOUT_MIN=${AGENT_TIMEOUT_MIN:-70}
mkdir -p "$R/results"
LOG=${LOG:-$R/results/run-all.log}

# label | kind | fault | variant | lead min | restart | extra env
QUEUE=(
  "FC5-delistedBestseller|custom|delist-bestseller.sh|-|15|-|"
  "F5-adManualGc|flag|adManualGc|on|15|ad|"
  "FAI-assistantRetries|custom|ai-retries.sh|-|20|-|QUESTIONS=$A/questions-ai-cost.md"
  "FC2-cpuThrottle|custom|cpu-throttle.sh|-|15|-|"
  "FT4-earlyWarning|custom|thread-leak.sh|-|25|-|QUESTIONS=$A/questions-earlywarning.md"
  "F6-recommendationCacheFailure|flag|recommendationCacheFailure|on|15|recommendation|"
  "FS1-searchRelevance|custom|search-relevance.sh|-|20|-|QUESTIONS=$A/questions-search.md"
  "FT1-optimise|custom|no-fault.sh|-|15|-|QUESTIONS=$A/questions-optimise.md"
  "F3-kafkaQueue|custom|kafka-flood.sh|-|15|-|"
  "FT2-fiveFaults|custom|multi-fault.sh|-|25|-|QUESTIONS=$A/questions-explore.md AGENT_TIMEOUT_MIN=90"
)

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

run_entry() {  # $1 = queue entry, $2 = label suffix, $3 = progress tag
  local e=$1 sfx=$2 tag=$3 label kind fault variant lead restart extra
  IFS='|' read -r label kind fault variant lead restart extra <<<"$e"
  local entry="$label$sfx|$kind|$fault|$variant|$lead|$restart"
  log "=== [$tag] $label$sfx ($kind; lead $lead min; ${extra:-standard questions})"
  if [[ -n "${DRY:-}" ]]; then echo "  env $extra $A/run-scenario.sh \"$entry\""; return 0; fi
  # Containers outside the shop add host CPU and container metrics to every
  # platform's budget. Stop any listed in STOP_CONTAINERS that are running.
  for oc in ${STOP_CONTAINERS:-}; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$oc" 2>/dev/null)" == true ]]; then
      log "  $oc was running: stopping it"; docker update --restart=no "$oc" >/dev/null; docker stop "$oc" >/dev/null
    fi
  done
  # Kafka's memory creeps toward its 620 MiB limit over hours, and the Kafka
  # flood's prep refuses to start above 610 MiB. Restart Kafka, then checkout,
  # in the cool-down before the Kafka flood and the five faults.
  if [[ $label == F3-* || $label == FT2-* ]]; then
    log "  restarting kafka and checkout before $label (memory creep guard)"
    $A/ops.sh dc restart kafka >/dev/null 2>&1; sleep 40
    $A/ops.sh dc restart checkout >/dev/null 2>&1; sleep 30
    $A/ops.sh kafka-check 2>&1 | sed 's/^/  /' | tee -a "$LOG"
  fi
  # Checkout's Kafka producer can stick after a Kafka restart; restart
  # checkout if the proxy returned more than 20 504s in the last 5 min.
  local n504; n504=$(docker logs --since 5m frontend-proxy 2>&1 | grep -c '" 504 ')
  if (( n504 > 20 )); then log "  $n504 proxy 504s in 5 min: restarting checkout"; docker restart checkout >/dev/null; fi
  local before rc; before=$(wc -l < "$LOG" 2>/dev/null || echo 0)
  env $extra "$A/run-scenario.sh" "$entry" >> "$LOG" 2>&1; rc=$?
  log "=== [$tag] $label$sfx runner exited ($rc)"
  if (( rc != 0 )) || tail -n +"$((before+1))" "$LOG" 2>/dev/null | grep -qE "NOT VERIFIED|SKIP |preflight: FAIL|agents still running after|PREP FAILED|DELIVERY FAILED"; then
    log "  FAILED-RUN $label$sfx: queued for one retry at the end"; return 1
  fi
  return 0
}

FROM=${FROM:-1}; TO=${TO:-${#QUEUE[@]}}
log "=== queue start (pid $$), ${#QUEUE[@]} scenarios, entries $FROM-$TO, REPS=$REPS MAXPAR=$MAXPAR"
i=0; RETRY=()
for e in "${QUEUE[@]}"; do
  i=$((i+1)); (( i < FROM || i > TO )) && continue
  run_entry "$e" "" "$i/${#QUEUE[@]}" || RETRY+=("$e")
done
# One retry per failed run, under the suffix R, so the failed run's
# transcripts stay for inspection.
j=0
for e in ${RETRY[@]+"${RETRY[@]}"}; do
  j=$((j+1)); run_entry "$e" "R" "retry $j/${#RETRY[@]}" || log "  retry of ${e%%|*} failed again"
done
log "=== queue done (retried ${#RETRY[@]})"
