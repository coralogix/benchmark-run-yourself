# Reads the harness settings from the folder's .env into shell variables,
# without exporting them, so platform keys never reach an agent's
# environment. A variable already set in the caller's environment wins.
# Sourced by lib.sh and preflight.sh.
_env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
if [[ -f "$_env_file" ]]; then
  for _name in AGENT_HOME RESULTS_DIR AGENT_MODEL AGENT_EFFORT CLAUDE_SANDBOX_TMP \
               CX_PROFILE DD_SITE DATADOG_SITE DTCTL_CONTEXT DTCTL_LOCK_FILE GCX_CONTEXT NR_PROFILE NEWRELIC_ACCOUNT_ID \
               STOP_CONTAINERS REVIEW_MODEL REVIEW_EFFORT REVIEW_MAXPAR GROUND_TRUTH; do
    [[ -n "${!_name:-}" ]] && continue
    _line=$(grep -E "^${_name}=" "$_env_file" | tail -1) || true
    [[ -n "$_line" ]] || continue
    _value=${_line#*=}
    _value=${_value%\"}; _value=${_value#\"}; _value=${_value%\'}; _value=${_value#\'}
    printf -v "$_name" '%s' "$_value"
  done
fi
unset _env_file _name _line _value
