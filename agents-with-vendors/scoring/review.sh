#!/usr/bin/env bash
# The reviewing agent: marks every blinded transcript in results/blind against
# the rubric and the reference measurements, one transcript per session, in
# random order. It sees only the blinded transcript, the rubric and the
# scenario's section of results/ground-truth.yaml, never the ID map. Each
# review goes to results/blind/reviews/<ID>.json; reviews already there are
# kept, so a stopped run resumes.
#
#   scoring/review.sh                 review every transcript not yet reviewed
#   REVIEW_MAXPAR=4 scoring/review.sh run four reviews at once (default 2)
#
# Settings: REVIEW_MODEL (default AGENT_MODEL, else claude-opus-5-5),
# REVIEW_EFFORT (default high), GROUND_TRUTH (default results/ground-truth.yaml).
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$R/agents/env.sh"
B="$R/results/blind"
MODEL="${REVIEW_MODEL:-${AGENT_MODEL:-claude-opus-5-5}}"
EFFORT="${REVIEW_EFFORT:-high}"
TRUTH="${GROUND_TRUTH:-$R/results/ground-truth.yaml}"
MAXPAR="${REVIEW_MAXPAR:-2}"
[[ -d "$B/transcripts" ]] || { echo "run: python3 scoring/blind.py export" >&2; exit 1; }
[[ -f "$TRUTH" ]] || { echo "fill in $TRUTH first (from scoring/ground-truth-template.yaml)" >&2; exit 1; }
mkdir -p "$B/reviews"
# An empty working directory and no tools, MCP servers or memory: the
# reviewer reads only its input.
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
echo '{"mcpServers":{}}' > "$W/mcp.json"

review() {
  local id="$1" raw="$B/reviews/$1.raw.json"
  python3 "$R/scoring/blind.py" prompt "$id" "$B" "$TRUTH" | (
    cd "$W" && env -u CLAUDECODE CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
      claude -p --restricted --tools "" --strict-mcp-config --mcp-config "$W/mcp.json" \
        --model "$MODEL" --effort "$EFFORT" --output-format json
  ) > "$raw" 2> "$B/reviews/$id.stderr" || { echo "FAIL $id (see $B/reviews/$id.stderr)"; return 0; }
  python3 - "$raw" "$B/reviews/$id.json" <<'PY' && echo "ok   $1" || echo "FAIL $1: no JSON object in the reply"
import json, re, sys
reply = json.load(open(sys.argv[1])).get("result", "")
m = re.search(r"\{.*\}", reply, re.S)
review = json.loads(m.group(0))
assert isinstance(review.get("marks"), dict)
json.dump(review, open(sys.argv[2], "w"), indent=1)
PY
}

ids=$(ls "$B/transcripts" | sed 's/\.md$//' | python3 -c 'import random,sys; a=sys.stdin.read().split(); random.shuffle(a); print(" ".join(a))')
n=0
for id in $ids; do
  [[ -f "$B/reviews/$id.json" ]] && continue
  review "$id" &
  n=$((n+1))
  (( n % MAXPAR == 0 )) && wait
done
wait
python3 "$R/scoring/blind.py" collect "$B"
