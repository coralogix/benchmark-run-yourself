#!/bin/bash
# Scenario "search relevance": a stricter search matcher ships.
# Search starts requiring the whole query as a phrase in a product's name or
# categories, so most multi-word searches ("solar telescope", "bird watching
# binoculars") return nothing; single-word searches still work. No errors, no
# latency change; shoppers who get no results leave, so fewer orders.
# Needs the search service (otel-demo/compose.search.yaml), the
# frontend-proxy route and the load-generator search task
# (shop/patches/30-search.patch and 20-load-generator.patch).
#
#   search-relevance.sh prep    write match_mode=any, check the service is up and serving results
#   search-relevance.sh on      write match_mode=phrase; verify it reads back
#   search-relevance.sh off     write match_mode=any; verify it reads back
#   search-relevance.sh status  config file, plus one probe search of each kind
#
# The config is re-read per request (by mtime), so no restart; written with docker cp.
set -uo pipefail
C=search
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ts() { date -u +%H:%M:%SZ; }
# The service re-reads the file only when its mtime changes, and docker cp keeps
# 1-second mtimes: two writes in the same second (prep then on, for example)
# would leave the old mode cached. Wait 2 s so each write gets a new mtime.
write_conf() { sleep 2; printf 'match_mode=%s\n' "$1" > "$TMP/search.conf" && docker cp "$TMP/search.conf" $C:/tmp/search.conf; }
read_conf() { docker cp $C:/tmp/search.conf - 2>/dev/null | tar -xO 2>/dev/null | sed -n 's/^match_mode=//p'; }
# Probe from inside the search container (python is there; no curl). Probes are
# untraced (no traceparent) and go straight to the service, not via the proxy,
# so they don't show up as shopper traffic in proxy logs; they do create
# search_products spans, so keep them to a minimum.
probe() {
  docker exec $C /venv/bin/python -c "import json,sys,urllib.request as u;r=u.urlopen(u.Request('http://localhost:8090/api/search',data=json.dumps({'query':sys.argv[1]}).encode(),headers={'Content-Type':'application/json'}),timeout=5);print(len(json.load(r)['results']))" "$1" 2>/dev/null
}

case "${1:-}" in
  prep)
    docker inspect -f '{{.State.Running}}' $C 2>/dev/null | grep -q true || { echo "PREP FAILED: $C not running (deploy compose.search.yaml first)"; exit 1; }
    write_conf any || { echo "PREP FAILED: write"; exit 1; }
    n=$(probe "solar telescope")
    [[ "$(read_conf)" == any && "${n:-0}" -gt 0 ]] && echo "PREP match_mode=any at $(ts) (probe: $n results)" \
      || { echo "PREP FAILED: mode '$(read_conf)', probe '${n:-?}'"; exit 1; } ;;
  on)
    write_conf phrase || { echo "NOT VERIFIED: write failed"; exit 1; }
    n=$(probe "solar telescope")
    [[ "$(read_conf)" == phrase && "${n:-x}" == 0 ]] && echo "VERIFIED match_mode=phrase at $(ts) (probe: 0 results)" \
      || { echo "NOT VERIFIED: mode reads '$(read_conf)', probe '${n:-?}'"; exit 1; } ;;
  off)
    write_conf any || { echo "NOT VERIFIED off: write failed"; exit 1; }
    n=$(probe "solar telescope")
    [[ "$(read_conf)" == any && "${n:-0}" -gt 0 ]] && echo "VERIFIED match_mode=any at $(ts) (probe: $n results)" \
      || { echo "NOT VERIFIED off: mode reads '$(read_conf)', probe '${n:-?}'"; exit 1; } ;;
  status)
    echo "match_mode=$(read_conf) (file; absent = any)"
    echo "probe 'telescope': $(probe telescope) results; 'solar telescope': $(probe 'solar telescope') results" ;;
  *) echo "usage: $0 prep|on|off|status" >&2; exit 2 ;;
esac
