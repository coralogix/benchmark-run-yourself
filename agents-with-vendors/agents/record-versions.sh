#!/usr/bin/env bash
# Records the versions behind a run in a JSON file: the agent runtime (Claude Code) and
# its model and effort, each platform's CLI version, the skills each agent
# loads (names and a checksum of each platform's plugin), the shop's commit
# and image tag, every running container's image and digest, and the host.
# run-agents-rep.sh calls it before every launch and stores the file as
# versions.json in each replicate's results directory.
#
#   agents/record-versions.sh <out.json>
set -uo pipefail
source "$(dirname "$0")/lib.sh"
out="$1"

v() { "$@" 2>/dev/null | head -1 | tr -d '\r'; }
SHOP="${SHOP_DIR:-$ROOT/otel-demo}"

skills_json() {  # one line per platform: platform<TAB>skill names<TAB>checksum
  local p d sum names
  for p in coralogix datadog dynatrace grafana newrelic; do
    d="$BASE/$p/cli-plugin"
    if [[ -d "$d" ]]; then
      names=$(find "$d" -name SKILL.md -exec dirname {} \; | xargs -n1 basename 2>/dev/null | sort | tr '\n' ',')
      sum=$(cd "$d" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 cat | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
    else
      names=""; sum=""
    fi
    printf '%s\t%s\t%s\n' "$p" "$names" "$sum"
  done
}

containers() {  # name<TAB>image<TAB>image id<TAB>repo digests
  local c
  for c in $(docker ps --format '{{.Names}}' 2>/dev/null | sort); do
    docker inspect -f "$c"$'\t''{{.Config.Image}}'$'\t''{{.Image}}' "$c" 2>/dev/null | tr -d '\n'
    printf '\t%s\n' "$(docker image inspect -f '{{join .RepoDigests ","}}' "$(docker inspect -f '{{.Image}}' "$c" 2>/dev/null)" 2>/dev/null)"
  done
}

python3 - "$out" \
  "$(v claude --version)" "$MODEL" "$EFFORT" \
  "$(v cx --version)" "$(v pup --version)" "$(v dtctl version)" "$(v gcx --version)" "$(v newrelic version)" \
  "$(git -C "$SHOP" rev-parse HEAD 2>/dev/null)" \
  "$(grep -h '^DEMO_VERSION=' "$SHOP/.env" "$SHOP/.env.override" 2>/dev/null | tail -1 | cut -d= -f2)" \
  "$(uname -sr)" "$(v docker version --format '{{.Server.Version}}')" \
  "$(skills_json)" "$(containers)" <<'PY'
import json, sys, time
(out, claude, model, effort, cx, pup, dtctl, gcx, nr, commit, demo, host, docker,
 skills, containers) = sys.argv[1:]
rec = {
    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "agent_runtime": {"claude_code": claude, "model": model, "effort": effort},
    "clis": {"cx": cx, "pup": pup, "dtctl": dtctl, "gcx": gcx, "newrelic": nr},
    "skills": {},
    "shop": {"commit": commit, "demo_version": demo},
    "host": {"os": host, "docker": docker},
    "containers": {},
}
for line in skills.splitlines():
    p, names, digest = (line.split("\t") + ["", ""])[:3]
    rec["skills"][p] = {"skills": [n for n in names.split(",") if n], "plugin_sha256": digest}
for line in containers.splitlines():
    parts = line.split("\t")
    if len(parts) >= 3:
        rec["containers"][parts[0]] = {"image": parts[1], "image_id": parts[2],
                                       "repo_digests": [d for d in (parts[3] if len(parts) > 3 else "").split(",") if d]}
json.dump(rec, open(out, "w"), indent=1)
PY
echo "versions: $out"
