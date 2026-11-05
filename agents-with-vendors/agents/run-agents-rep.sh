#!/usr/bin/env bash
# Launches REPS agents per platform at once, all against the same fault
# window. Each agent gets the system prompt and asks the questions in
# QUESTIONS one at a time, in one conversation.
#
#   WINDOW="the period from <start> to <end> (UTC)" QUESTIONS=agents/questions.md \
#     REPS=5 MAXPAR=15 STAGGER_S=20 agents/run-agents-rep.sh <label> [platform ...]
#
# Replicate r gets its own agent dir (r1 = $AGENT_HOME, rN = $AGENT_HOME-rN,
# with the CLI plugin copied in), so replicates never share a work dir.
# Transcripts: $RESULTS_DIR/<label>-r<N>/<platform>.jsonl, with versions.json
# (agents/record-versions.sh) beside them.
# Launches are staggered by STAGGER_S seconds (default 20), because agents
# starting at once stall the host. The order of all REPS x platforms launches
# is shuffled every run, so no platform or replicate is always first or last.
# MAXPAR (default 0 = no cap): never more than MAXPAR agents running at once.
# A launch waits for a running agent to finish; every agent uses the same
# fixed WINDOW.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

label="$1"; shift
platforms=("$@")
[[ ${#platforms[@]} -eq 0 ]] && platforms=(coralogix datadog dynatrace grafana newrelic)
REPS=${REPS:-5}
STAGGER_S=${STAGGER_S:-20}
MAXPAR=${MAXPAR:-0}
BASE0="$BASE"

questions=()
while IFS= read -r -d '' q; do questions+=("$q"); done < <(python3 - "${QUESTIONS:-$HERE/questions.md}" <<'EOF'
import re, sys
for q in re.split(r'(?m)^---\s*$', open(sys.argv[1]).read()):
    if q.strip(): sys.stdout.write(q.strip() + "\0")
EOF
)
[[ ${#questions[@]} -eq 0 ]] && { echo "no questions in ${QUESTIONS:-$HERE/questions.md}" >&2; exit 1; }

mkdir -p "$RESULTS_DIR"
# Record the runtime, CLI, skill, shop and image versions with every replicate.
"$HERE/record-versions.sh" "$RESULTS_DIR/$label.versions.json" >/dev/null
for r in $(seq 1 "$REPS"); do
  mkdir -p "$RESULTS_DIR/$label-r$r"; cp "$RESULTS_DIR/$label.versions.json" "$RESULTS_DIR/$label-r$r/versions.json"
done
rm -f "$RESULTS_DIR/$label.versions.json"
pairs=()
for r in $(seq 1 "$REPS"); do for p in "${platforms[@]}"; do pairs+=("$r:$p"); done; done
read -ra pairs <<<"$(python3 -c 'import random,sys; a=sys.argv[1:]; random.shuffle(a); print(" ".join(a))' "${pairs[@]}")"
echo "launch order: ${pairs[*]} (stagger ${STAGGER_S}s, max parallel ${MAXPAR:-0})"
running() { local c=0 q; for q in ${pids[@]+"${pids[@]}"}; do kill -0 "$q" 2>/dev/null && c=$((c+1)); done; echo "$c"; }
pids=(); n=0
for pr in "${pairs[@]}"; do
    r=${pr%%:*}; p=${pr#*:}
    if (( n++ > 0 && STAGGER_S > 0 )); then sleep "$STAGGER_S"; fi
    if (( MAXPAR > 0 )); then
      waited=0
      while (( $(running) >= MAXPAR )); do sleep 5; waited=$((waited+5)); done
      (( waited > 0 )) && echo "  slot free after ${waited}s of waiting (cap $MAXPAR)"
    fi
    (
      BASE="$BASE0"; (( r > 1 )) && BASE="$BASE0-r$r"
      if (( r > 1 )) && [[ -d "$BASE0/$p/cli-plugin" ]]; then
        mkdir -p "$BASE/$p"; rm -rf "$BASE/$p/cli-plugin"; cp -R "$BASE0/$p/cli-plugin" "$BASE/$p/cli-plugin"
      fi
      mkdir -p "$BASE/$p"
      out="$RESULTS_DIR/$label-r$r"; mkdir -p "$out"
      write_platform_files "$p"
      dir="$BASE/$p"
      sid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
      echo "$sid" > "$out/$p.session"
      : > "$out/$p.jsonl"; : > "$out/$p.stderr"
      render_template "$HERE/system-prompt.md" > "$dir/system-prompt.md"
      kind=new
      for q in "${questions[@]}"; do
        run_turn "$p" "$out" "$sid" "$kind" "$q" "$dir/system-prompt.md"
        kind=resume
      done
      jsonl_marker "$out/$p.jsonl" done
    ) &
    pids+=($!)
    echo "started $p r$r (pid $!) to $RESULTS_DIR/$label-r$r/$p.jsonl"
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
echo "all agents finished: $RESULTS_DIR/$label-r{1..$REPS}"
