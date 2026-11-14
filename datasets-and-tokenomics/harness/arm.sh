#!/bin/bash
# One isolated opencode server per arm, each with its own HOME, config and AGENTS.md.
#   harness/arm.sh setup|start|stop <none|discover|prompt>
# Needs (from .env): CX_API_KEY, CX_REGION, RUN_START, MODEL_ID, MODEL_BASE_URL, MODEL_KEY_FILE.
set -euo pipefail
CMD=$1; ARM=$2
H=$(cd "$(dirname "$0")" && pwd); R=${ARMS_DIR:-/tmp/datasets-and-tokenomics}/$ARM
case $ARM in none) PORT=47201;; prompt) PORT=47202;; discover) PORT=47203;; *) echo "unknown arm $ARM"; exit 1;; esac

# The no-dataset arm may not list datasets or name one; raw logs and spans stay open. The last matching rule wins.
NO_DATASETS=""
if [ "$ARM" = none ]; then
  NO_DATASETS='"*default/*": "deny", "*default/logs*": "allow", "*default/spans*": "allow", "*datasets*": "deny",'
  for d in $(python3 -c "import sys; sys.path.insert(0, '$H/../datasets'); from queries import DATASETS; print(' '.join(DATASETS))"); do
    NO_DATASETS="$NO_DATASETS \"*$d*\": \"deny\","
  done
fi

case $CMD in
setup)
  rm -rf "$R"; mkdir -p "$R/home/.config/opencode" "$R/work"
  cat > "$R/home/.config/opencode/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "bench/$MODEL_ID", "small_model": "bench/$MODEL_ID", "autoupdate": false, "share": "disabled",
  "provider": {"bench": {"npm": "@ai-sdk/openai-compatible", "name": "Benchmark model",
    "options": {"baseURL": "$MODEL_BASE_URL", "apiKey": "{file:$MODEL_KEY_FILE}", "timeout": 600000},
    "models": {"$MODEL_ID": {"name": "$MODEL_ID", "tool_call": true, "limit": {"context": 250000, "input": 212500, "output": 32768}}}}},
  "compaction": {"auto": true},
  "permission": {
    "edit": "deny", "webfetch": "deny", "websearch": "deny", "external_directory": "deny", "task": "deny",
    "bash": {
      "*": "deny",
      "cx *": "allow", "cx": "allow",
      "jq *": "allow", "python3 *": "allow", "head *": "allow", "tail *": "allow", "grep *": "allow", "wc *": "allow",
      "sort *": "allow", "uniq *": "allow", "awk *": "allow", "cut *": "allow", "echo *": "allow", "bc *": "allow", "date *": "allow", "tr *": "allow", "column *": "allow",
      $NO_DATASETS
      "*curl*": "deny", "*wget*": "deny", "*api/v1*": "deny", "*results/*": "deny", "*scenarios/*": "deny"
    }
  }
}
JSON
  python3 -c "import sys; sys.path.insert(0, '$H'); from common import render; print(render(open(sys.argv[1]).read()), end='')" \
    "$H/agents/AGENTS.$ARM.md" > "$R/work/AGENTS.md"
  echo "set up $ARM in $R";;
start)
  cd "$R/work"
  env -i HOME="$R/home" PATH="$(dirname "$(command -v cx)"):$(dirname "$(command -v opencode)"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin" \
    TERM=xterm SHELL=/bin/bash CX_API_KEY="$CX_API_KEY" CX_REGION="$CX_REGION" \
    OPENCODE_SERVER_PASSWORD="bench-$ARM" OPENCODE_DISABLE_CLAUDE_CODE=1 OPENCODE_DISABLE_AUTOUPDATE=1 \
    nohup opencode serve --port $PORT --hostname 127.0.0.1 > "$R/server.log" 2>&1 &
  echo $! > "$R/pid"; echo "started $ARM on port $PORT (pid $(cat "$R/pid"))";;
stop)
  [ -f "$R/pid" ] && kill "$(cat "$R/pid")" 2>/dev/null || true;;
esac
