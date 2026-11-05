#!/bin/bash
# Scenario "AI cost": the assistant's compare-products feature switches to
# prompt template v2 (a friendlier rewrite without the "JSON only"
# instruction). The model then sometimes wraps its JSON in
# prose, the parser fails, and the agent quietly asks again (up to 3
# follow-ups). Shoppers still get answers; token spend and LLM calls per
# comparison rise. Needs the AI layer (otel-demo/compose.ai.yaml: agent and
# llm-gateway, from shop/patches/40-ai-assistant.patch) and the
# load-generator compare task (shop/patches/20-load-generator.patch).
#
#   ai-retries.sh prep    check agent and llm-gateway are up; template v1
#   ai-retries.sh on      template v2; verify it reads back
#   ai-retries.sh off     template v1; verify
#   ai-retries.sh status  template, containers
#
# The template file is read on every request, so no restart is needed.
set -uo pipefail
C=agent
F=/tmp/compare_prompt_version
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ts() { date -u +%H:%M:%SZ; }
write_v() { printf '%s\n' "$1" > "$TMP/v" && docker cp "$TMP/v" $C:$F; }
read_v() { docker cp $C:$F - 2>/dev/null | tar -xO 2>/dev/null | tr -d '[:space:]'; }
up() { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == true ]]; }

case "${1:-}" in
  prep)
    up $C && up llm-gateway || { echo "PREP FAILED: agent or llm-gateway not running (deploy compose.ai.yaml first)"; exit 1; }
    write_v v1 && [[ "$(read_v)" == v1 ]] && echo "PREP template=v1 at $(ts)" || { echo "PREP FAILED"; exit 1; } ;;
  on)
    write_v v2 || { echo "NOT VERIFIED: write failed"; exit 1; }
    [[ "$(read_v)" == v2 ]] && echo "VERIFIED template=v2 at $(ts)" || { echo "NOT VERIFIED: reads '$(read_v)'"; exit 1; } ;;
  off)
    write_v v1
    [[ "$(read_v)" == v1 ]] && echo "VERIFIED template=v1 at $(ts)" || { echo "NOT VERIFIED off: reads '$(read_v)'"; exit 1; } ;;
  status)
    echo "template: $(read_v)"
    for c in $C llm-gateway; do echo "$c running=$(docker inspect -f '{{.State.Running}}' $c 2>/dev/null)"; done ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
