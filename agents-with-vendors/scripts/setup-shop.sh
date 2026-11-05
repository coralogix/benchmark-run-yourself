#!/bin/bash
# Fetches the OpenTelemetry Astronomy Shop at a pinned commit into otel-demo/,
# applies the benchmark's changes (shop/patches/), builds the changed images
# and the latency helper image, starts the stack and tags Kafka's image for the
# Kafka admin helper.
#
#   scripts/setup-shop.sh            fetch, patch, build, start
#   NO_START=1 scripts/setup-shop.sh fetch, patch and build only
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM=https://github.com/open-telemetry/opentelemetry-demo.git
COMMIT=dedc0178918e260823323b8d95005a8cb924b007
D="$R/otel-demo"

[[ -f "$R/.env" ]] || { echo "copy .env.example to .env and fill it in first" >&2; exit 1; }

if [[ ! -d "$D/.git" ]]; then
  git clone --quiet "$UPSTREAM" "$D"
  git -C "$D" checkout --quiet "$COMMIT"
  for p in "$R"/shop/patches/*.patch; do
    git -C "$D" apply "$p"
    echo "applied $(basename "$p")"
  done
else
  echo "otel-demo/ exists; leaving it as it is (delete it to fetch again)"
fi

O="$R/agents/ops.sh"
# Services the patches change, plus the two services the patches add.
"$O" dc build fraud-detection frontend-proxy load-generator agent llm-gateway search
# The latency helper (agents/custom/latency-bursts.sh). The collector hides
# harness-* images from docker_stats.
docker build -q -t harness-nettools:1 -f "$R/agents/custom/Dockerfile.nettools" "$R/agents/custom"

if [[ -z "${NO_START:-}" ]]; then
  "$O" dc up -d
  # The Kafka admin tools run from Kafka's own image, tagged so the collector
  # hides those containers too (agents/ops.sh kafka-check, kafka-reset).
  docker tag "$(docker inspect -f '{{.Config.Image}}' kafka)" harness-kafka-admin:1
  "$O" dc ps
fi
