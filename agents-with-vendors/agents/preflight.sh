#!/usr/bin/env bash
# Run right before each investigation, outside the agent sandbox. One cheap
# query per CLI refreshes its login token here (a refresh inside the sandbox
# can delete Dynatrace's stored token) and confirms each CLI is scoped to the
# benchmark's account. On any FAIL, fix that login before launching agents.
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$R/agents/env.sh"
CX_PROFILE="${CX_PROFILE:-competitor-comparison}"
DTCTL_CONTEXT="${DTCTL_CONTEXT:-competitor-comparison-readonly}"
GCX_CONTEXT="${GCX_CONTEXT:-competitor-comparison}"
NR_PROFILE="${NR_PROFILE:-competitor-comparison}"
: "${NEWRELIC_ACCOUNT_ID:?set NEWRELIC_ACCOUNT_ID in .env}"
check() { local name="$1"; shift; if out=$("$@" 2>&1); then echo "OK   $name"; else echo "FAIL $name: $(echo "$out" | tail -1 | cut -c1-160)"; fi; }

check coralogix  cx --profile "$CX_PROFILE" logs 'limit 1' --tier archive --start now-15m --limit 1
# pup tokens last about 1 h and can't be renewed inside the agent sandbox:
# refresh here first so a run never starts with only minutes left.
pup auth refresh >/dev/null 2>&1 || true
check datadog    pup logs search --query='*' --from=15m --limit=1
check dynatrace  env DTCTL_TOKEN_STORAGE=file DTCTL_CONTEXT="$DTCTL_CONTEXT" dtctl query 'fetch logs, from:now()-15m | limit 1'
# gcx OAuth refresh tokens last 30 days, then need a browser login. A Viewer
# service-account token (gcx login --token) does not expire; with OAuth,
# this warns a week ahead.
gcx_exp=$(gcx config view 2>/dev/null | grep -o '"oauth-refresh-expires-at":"[^"]*"' | head -1 | cut -d'"' -f4)
if [[ -n "$gcx_exp" ]]; then
  left=$(python3 -c 'import sys,time,calendar; print(int((calendar.timegm(time.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ")) - time.time()) // 86400))' "$gcx_exp" 2>/dev/null || echo 0)
  (( left < 7 )) && echo "WARN grafana: OAuth refresh expires in ${left} days ($gcx_exp); run: gcx login $GCX_CONTEXT --oauth"
fi
check grafana    gcx --context "$GCX_CONTEXT" metrics query 'count(up)'
check newrelic   newrelic nrql query --profile "$NR_PROFILE" --accountId "$NEWRELIC_ACCOUNT_ID" --query 'SELECT count(*) FROM Log SINCE 15 minutes ago'
pup auth status 2>&1 | grep -i 'expires in' | sed 's/^/     datadog token /'
