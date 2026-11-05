# Shared by run-agents-rep.sh. Not run on its own.
#
# Each agent turn is `claude -p --restricted` from a directory under
# $AGENT_HOME/<platform>/work that contains only its CLI's plugin, runs Bash
# inside a sandbox that can only reach that platform's API domains (no
# localhost, no Docker socket, no home directory except the CLI's own
# config), has only the Bash, Skill, Read and Agent tools, and no MCP servers,
# memory or web. Turns are appended to $RESULTS_DIR/<label>-r<N>/<platform>.jsonl (stream-json),
# each preceded by a {"type":"question"} line.
#
# The CLIs' config directories follow the host's conventions: on macOS
# ~/Library/Application Support (and the login keychain), on Linux
# $XDG_CONFIG_HOME (default ~/.config). Every platform gets the same system
# prompt (agents/system-prompt.md) with only CLI, PLATFORM, SCOPE and WINDOW
# filled in, and the skills its own CLI ships (agents/build-cli-plugins.sh).
# SCOPE names the profile, context, site or account the CLI must use and
# nothing else.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
source "$HERE/env.sh"
BASE="${AGENT_HOME:-$HOME/vendor-agents}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/runs}"
MODEL="${AGENT_MODEL:-claude-opus-5-5}"
EFFORT="${AGENT_EFFORT:-high}"
# Claude Code's sandbox points commands' $TMPDIR here; it is shared by every agent.
SANDBOX_TMP="${CLAUDE_SANDBOX_TMP:-/tmp/claude-$(id -u)}"

CX_PROFILE="${CX_PROFILE:-competitor-comparison}"
DTCTL_CONTEXT="${DTCTL_CONTEXT:-competitor-comparison-readonly}"
GCX_CONTEXT="${GCX_CONTEXT:-competitor-comparison}"
NR_PROFILE="${NR_PROFILE:-competitor-comparison}"
DD_SITE="${DD_SITE:-${DATADOG_SITE:-}}"

platform_config() {
  # Sets: CLI PLATFORM SCOPE DENY DOMAINS READ_DIRS WRITE_DIRS EXTRA_ENV
  local AS KEYCHAIN=()
  if [[ "$(uname -s)" == Darwin ]]; then
    AS="$HOME/Library/Application Support"; KEYCHAIN=("$HOME/Library/Keychains")
  else
    AS="${XDG_CONFIG_HOME:-$HOME/.config}"
  fi
  EXTRA_ENV=()
  case "$1" in
    coralogix)
      CLI=cx; PLATFORM=Coralogix
      SCOPE="\`--profile $CX_PROFILE\` (every \`cx\` command must start with \`cx --profile $CX_PROFILE\`)"
      DENY='["Bash(cx profiles*)","Bash(cx *profiles *)","Bash(cx *--yes*)","Bash(cx *--api-key*)","Bash(cx *--profile default*)","Bash(cx *--profile *--profile*)","Bash(cx *create*)","Bash(cx *delete*)","Bash(cx *update*)","Bash(cx *replace*)","Bash(cx *apply*)","Bash(cx * enable *)","Bash(cx * disable *)","Bash(cx * reorder*)"]'
      DOMAINS='["coralogix.com","*.coralogix.com","*.*.coralogix.com"]'
      READ_DIRS="[\"$HOME/.cx\"]"
      WRITE_DIRS="[\"$HOME/.cx\"]"
      EXTRA_ENV=(CX_PROFILE="$CX_PROFILE") ;;
    datadog)
      : "${DD_SITE:?set DD_SITE in .env}"
      CLI=pup; PLATFORM=Datadog
      SCOPE="the Datadog site \`$DD_SITE\`"
      DENY='["Bash(pup *--yes*)","Bash(pup auth *)","Bash(pup *create*)","Bash(pup *delete*)","Bash(pup *update*)"]'
      DOMAINS='["datadoghq.com","*.datadoghq.com","*.*.datadoghq.com"]'
      READ_DIRS="[\"$AS/pup\"]"
      WRITE_DIRS="[\"$AS/pup\"]" ;;
    dynatrace)
      CLI=dtctl; PLATFORM=Dynatrace
      SCOPE='its preconfigured read-only context (no `--context` flag needed)'
      DENY='["Bash(dtctl *--context*)","Bash(dtctl config*)","Bash(dtctl ctx*)","Bash(dtctl apply*)","Bash(dtctl create*)","Bash(dtctl delete*)","Bash(dtctl edit*)"]'
      DOMAINS='["*.dynatrace.com","*.*.dynatrace.com"]'
      # dtctl takes its token-refresh lock file in $TMPDIR, which is denied to
      # every agent; DTCTL_LOCK_FILE (optional) admits that one file, so
      # refreshes stay locked. Find its name with: ls "$SANDBOX_TMP" | grep dtctl
      READ_DIRS="[\"$AS/dtctl\""
      if [[ -n "${DTCTL_LOCK_FILE:-}" ]]; then
        READ_DIRS+=", \"$SANDBOX_TMP/$DTCTL_LOCK_FILE\", \"/private$SANDBOX_TMP/$DTCTL_LOCK_FILE\""
      fi
      READ_DIRS+="]"
      WRITE_DIRS="[\"$AS/dtctl\"]"
      EXTRA_ENV=(DTCTL_CONTEXT="$DTCTL_CONTEXT" DTCTL_TOKEN_STORAGE=file) ;;
    grafana)
      CLI=gcx; PLATFORM="Grafana Cloud"
      SCOPE="\`--context $GCX_CONTEXT\` (every \`gcx\` command must start with \`gcx --context $GCX_CONTEXT\`)"
      DENY='["Bash(gcx *create*)","Bash(gcx *delete*)","Bash(gcx *update*)","Bash(gcx *push*)","Bash(gcx *apply*)","Bash(gcx *config *)","Bash(gcx *setup*)"]'
      DOMAINS='["grafana.com","*.grafana.com","*.*.grafana.com","*.grafana.net","*.*.grafana.net"]'
      # gcx keeps token-refresh locks under ~/.local/state/gcx and, on macOS,
      # its token in the login keychain, so both are writable.
      READ_DIRS="[\"$HOME/.config/gcx\", \"$HOME/.local/state/gcx\"]"
      WRITE_DIRS="[\"$HOME/.config/gcx\", \"$HOME/.local/state/gcx\"${KEYCHAIN[0]:+, \"${KEYCHAIN[0]}\"}]" ;;
    newrelic)
      : "${NEWRELIC_ACCOUNT_ID:?set NEWRELIC_ACCOUNT_ID in .env}"
      CLI=newrelic; PLATFORM="New Relic"
      SCOPE="\`--profile $NR_PROFILE\`, account ID \`$NEWRELIC_ACCOUNT_ID\` (pass \`--accountId $NEWRELIC_ACCOUNT_ID\` where a command needs an account)"
      DENY='["Bash(newrelic events*)","Bash(newrelic reporting*)","Bash(newrelic nerdstorage*)","Bash(newrelic apiAccess*)","Bash(newrelic install*)","Bash(newrelic migrate*)","Bash(newrelic config*)","Bash(newrelic profile*)","Bash(newrelic *create*)","Bash(newrelic *delete*)","Bash(newrelic *update*)","Bash(newrelic *mutation*)"]'
      DOMAINS='["newrelic.com","*.newrelic.com","*.*.newrelic.com"]'
      READ_DIRS="[\"$HOME/.newrelic\"]"
      WRITE_DIRS="[\"$HOME/.newrelic\"]" ;;
    *) echo "unknown platform $1" >&2; exit 1 ;;
  esac
}

# render_template <file>: fills {{CLI}} {{PLATFORM}} {{SCOPE}} {{WINDOW}} for
# the current platform_config.
render_template() {
  python3 - "$1" "$CLI" "$PLATFORM" "$SCOPE" "${WINDOW:-the last 45 minutes}" <<'PY'
import sys
t = open(sys.argv[1]).read()
for k, v in zip(("CLI", "PLATFORM", "SCOPE", "WINDOW"), sys.argv[2:]):
    t = t.replace("{{%s}}" % k, v)
print(t)
PY
}

# Bash is open inside the sandbox (network: platform domains only; no home
# directory except the CLI config; no Docker socket). These stay denied:
# local inspection, interpreters that could sidestep the rules, network tools.
LOCAL_DENY='"Bash(ps)","Bash(ps *)","Bash(pgrep *)","Bash(pkill *)","Bash(kill *)","Bash(top)","Bash(top *)","Bash(htop*)","Bash(lsof*)","Bash(env)","Bash(env *)","Bash(printenv*)","Bash(set)","Bash(export*)","Bash(hostname*)","Bash(uname*)","Bash(whoami*)","Bash(id)","Bash(id *)","Bash(ifconfig*)","Bash(ipconfig*)","Bash(netstat*)","Bash(arp*)","Bash(nslookup*)","Bash(dig *)","Bash(ping *)","Bash(traceroute*)","Bash(nc *)","Bash(curl *)","Bash(wget *)","Bash(ssh *)","Bash(scp *)","Bash(docker*)","Bash(kubectl*)","Bash(kind *)","Bash(launchctl*)","Bash(sysctl*)","Bash(system_profiler*)","Bash(defaults *)","Bash(osascript*)","Bash(open *)","Bash(python*)","Bash(node *)","Bash(perl*)","Bash(ruby*)","Bash(bash *)","Bash(sh *)","Bash(zsh *)","Bash(eval *)","Bash(exec *)","Bash(source *)","Bash(history*)","Bash(crontab*)","Bash(mdfind*)","Bash(security *)","Bash(find ~*)","Bash(ls /Users)","Bash(ls /Users/)","Bash(ls -la /Users)","Bash(find /Users -maxdepth*)","Bash(ls ~*)","Bash(cat ~*)","Bash(head ~*)","Bash(tail ~*)"'

# write_platform_files <platform>: fresh empty work dir, settings.json and
# mcp.json under $BASE/<platform>/.
write_platform_files() {
  platform_config "$1"
  local KEYCHAIN_JSON=""
  [[ "$(uname -s)" == Darwin ]] && KEYCHAIN_JSON=" \"$HOME/Library/Keychains\","
  local allow='["Bash(*)","Read(//'"$BASE/$1"'/cli-plugin/**)","Read(./**)"]'
  local deny="${DENY%]},$LOCAL_DENY]"
  local dir="$BASE/$1"
  rm -rf "$dir/work" "$dir/tmp"; mkdir -p "$dir/work"
  # Claude Code's own temp files go here (CLAUDE_CODE_TMPDIR in run_turn).
  # Commands' $TMPDIR is the shared $SANDBOX_TMP, which is denied below so
  # agents cannot read each other's scratch files; they keep scratch files in
  # their work dir.
  mkdir -m 700 -p "$dir/tmp"
  # The CLI's plugin is copied into the work dir: --restricted confines the
  # Read tool to it, and skills cite their own reference files.
  [[ -d "$dir/cli-plugin" ]] && cp -R "$dir/cli-plugin" "$dir/work/cli-plugin"
  cat > "$dir/settings.json" <<JSON
{
  "permissions": {
    "defaultMode": "dontAsk",
    "allow": $allow,
    "deny": $deny
  },
  "autoMemoryEnabled": false,
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": false,
    "enableWeakerNetworkIsolation": true,
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "denyRead": ["~/", "/var/run/docker.sock", "$SANDBOX_TMP", "/private$SANDBOX_TMP"],
      "allowRead": ${READ_DIRS%]},${KEYCHAIN_JSON} "$BASE/$1/cli-plugin", "$dir/work", "$dir/tmp"],
      "allowWrite": [".", "$dir/tmp", ${WRITE_DIRS#[}
    },
    "network": {
      "allowedDomains": $DOMAINS,
      "allowMachLookup": ["com.apple.SecurityServer", "com.apple.securityd.*"],
      "strictAllowlist": true
    }
  }
}
JSON
  echo '{"mcpServers":{}}' > "$dir/mcp.json"
}

# jsonl_marker <file> <type> [text]: a marker line in the transcript (not from claude).
jsonl_marker() {
  python3 -c 'import json,sys,time; print(json.dumps({"type":sys.argv[1],"text":sys.argv[2],"ts":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}))' "$2" "${3:-}" >> "$1"
}

# sessions_index_add <label> <platform> <session-id> <turn> <start> <end> <question>
# One row per turn in $RESULTS_DIR/sessions.csv, for matching the agents' own
# Claude Code telemetry (session.id) to scenario, platform and time window.
sessions_index_add() {
  python3 - "$RESULTS_DIR/sessions.csv" "$@" <<'PY'
import csv, os, sys
path, row = sys.argv[1], sys.argv[2:]
new = not os.path.exists(path)
with open(path, "a", newline="") as f:
    w = csv.writer(f)
    if new:
        w.writerow(["label", "platform", "session_id", "turn", "start_utc", "end_utc", "question"])
    w.writerow(row)
PY
}

# run_turn <platform> <outdir> <session-id> <new|resume> <question> [system-prompt-file]
# The first turn creates the session (with the system prompt, which Claude
# Code records and reuses on resume); later turns resume it.
run_turn() {
  local p="$1" out="$2" sid="$3" kind="$4" q="$5" sys="${6:-}"
  platform_config "$p"
  local dir="$BASE/$p"
  local PLUGIN_ARGS=()
  [[ -d "$dir/work/cli-plugin" ]] && PLUGIN_ARGS=(--plugin-dir "$dir/work/cli-plugin")
  local sess=(--resume "$sid")
  if [[ "$kind" == new ]]; then
    sess=(--session-id "$sid")
    [[ -n "$sys" ]] && sess+=(--append-system-prompt-file "$sys")
  fi
  jsonl_marker "$out/$p.jsonl" question "$q"
  local turn=$(( $(grep -c '"type": "question"' "$out/$p.jsonl") ))
  local t0; t0="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  (
    cd "$dir/work"
    env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 CLAUDE_CODE_TMPDIR="$dir/tmp" ${EXTRA_ENV[@]+"${EXTRA_ENV[@]}"} \
      claude -p "$q" "${sess[@]}" \
        --restricted --tools "Bash,Skill,Read,Agent" \
        ${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"} \
        --settings "$dir/settings.json" \
        --strict-mcp-config --mcp-config "$dir/mcp.json" \
        --permission-mode dontAsk \
        --model "$MODEL" --effort "$EFFORT" \
        --output-format stream-json --verbose \
        >> "$out/$p.jsonl" 2>> "$out/$p.stderr"
  ) || true
  sessions_index_add "$(basename "$out")" "$p" "$sid" "$turn" "$t0" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$q"
}
