#!/bin/bash
# Builds one Claude Code plugin per platform holding only the skills (and,
# for pup, subagents) that platform's CLI ships, from the CLI's own
# installer. The agents load it with --plugin-dir, under --restricted, so
# none of the user's personal ~/.claude/skills are visible (without
# --restricted every personal skill loads). New Relic's CLI ships no skills,
# so it gets no plugin.
#
#   agents/build-cli-plugins.sh     rebuilds $AGENT_HOME/<platform>/cli-plugin
set -euo pipefail
source "$(dirname "$0")/env.sh"
BASE="${AGENT_HOME:-$HOME/vendor-agents}"
B="$BASE/_skillbuild"
rm -rf "$B"; mkdir -p "$B"/{coralogix,datadog,dynatrace,grafana,grafana-home}

(cd "$B/coralogix" && cx skills install --local --agent claude-code >/dev/null)
(cd "$B/datadog"   && pup skills install claude --project >/dev/null)
(cd "$B/dynatrace" && dtctl skills install --for claude >/dev/null)
# gcx only installs to $HOME/.agents/skills; use a throwaway HOME.
HOME="$B/grafana-home" gcx agent skills install --all >/dev/null
mkdir -p "$B/grafana/.claude/skills" && cp -R "$B/grafana-home/.agents/skills/." "$B/grafana/.claude/skills/"

cli_of() {
  case "$1" in
    coralogix) echo cx ;;
    datadog) echo pup ;;
    dynatrace) echo dtctl ;;
    grafana) echo gcx ;;
  esac
}
version() {
  case "$1" in
    cx) cx --version ;;
    pup) pup --version 2>/dev/null | head -1 ;;
    dtctl) dtctl version 2>/dev/null | head -1 ;;
    gcx) gcx --version 2>/dev/null | head -1 ;;
  esac
}

for p in coralogix datadog dynatrace grafana; do
  P="$BASE/$p/cli-plugin"; rm -rf "$P"; mkdir -p "$P/.claude-plugin"
  cli=$(cli_of "$p")
  printf '{"name":"%s-cli","version":"1.0.0","description":"Skills shipped with the %s CLI"}\n' "$cli" "$cli" > "$P/.claude-plugin/plugin.json"
  [[ -d "$B/$p/.claude/skills" ]] && cp -R "$B/$p/.claude/skills" "$P/skills"
  [[ -d "$B/$p/.claude/agents" ]] && cp -R "$B/$p/.claude/agents" "$P/agents"
  echo "$p: $(find "$P/skills" -name SKILL.md 2>/dev/null | wc -l | tr -d ' ') skills, $(ls "$P/agents" 2>/dev/null | wc -l | tr -d ' ') agents, $(version "$cli" | tr '\n' ' ')"
done
rm -rf "$BASE/newrelic/cli-plugin"
echo "newrelic: no shipped skills"
