#!/bin/bash
# Build one Python 3.12 virtualenv per model family under envs/, each with pandas, numpy and torch 2.14.1.
# The timestral env also carries statsforecast, matplotlib and tabulate for the CPU pass and the reports.
# Usage: scripts/setup_envs.sh [family ...]   (default: all five, built in parallel)
set -u
cd "$(dirname "$0")/.."
TIMESTRAL_REF=${TIMESTRAL_REF:-a534bdbd244d64f3d9d743f46c7bf6fc330ed4ff}   # Timestral 1.0.0
COMMON="pandas numpy torch==2.14.1"
build() {
  n=$1; shift
  [ -x "envs/$n/bin/python" ] || uv venv -q "envs/$n" --python 3.12
  if uv pip install -q --python "envs/$n/bin/python" "$@" $COMMON; then echo "env $n ready"; else echo "env $n FAILED"; fi
}
families=${*:-timestral chronos t0 tirex timesfm}
for f in $families; do
  case $f in
    timestral) build timestral "git+https://github.com/mistralai/timestral@${TIMESTRAL_REF}" statsforecast==2.1.1 matplotlib tabulate & ;;
    chronos)   build chronos chronos-forecasting==2.3.2 & ;;
    t0)        build t0 tfc-t0==0.6.0 einops rotary_embedding_torch safetensors & ;;
    tirex)     build tirex tirex-2==0.3.1 & ;;
    timesfm)   build timesfm "timesfm[torch]==3.0.2" & ;;
    *) echo "unknown family $f"; exit 1 ;;
  esac
done
wait
