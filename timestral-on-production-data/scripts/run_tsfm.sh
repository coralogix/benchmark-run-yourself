#!/bin/bash
# Run the eight foundation models one per process, each in its own virtualenv, over every cutoff.
# Usage: scripts/run_tsfm.sh <device: cuda|cpu>   writes results/production/tsfm_<device>/
set -u
cd "$(dirname "$0")/.."
DEVICE=${1:?usage: run_tsfm.sh cuda|cpu}
export HF_HUB_DISABLE_PROGRESS_BARS=1
while read -r env model; do
  envs/$env/bin/python -W ignore scripts/bench_tsfm.py "$model" "$DEVICE" 2>&1 | grep -E "rows|Error|Traceback" | tail -3
done <<'LIST'
timestral Timestral-5M
timestral Timestral-28M
timestral Timestral-123M
timestral Timestral-209M
chronos Chronos-2
t0 t0-gamma
tirex TiRex-2
timesfm TimesFM-2.5
LIST
echo TSFM_DONE
