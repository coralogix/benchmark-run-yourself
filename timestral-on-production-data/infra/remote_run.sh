#!/bin/bash
# Runs on the GPU instance from the folder gpu_run.sh copied: build one virtualenv per model family,
# then run every foundation model on cuda.
set -u
cd "$(dirname "$0")/.."
export PATH=$HOME/.local/bin:$PATH
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
set -a; [ -f .env ] && . ./.env; set +a
scripts/setup_envs.sh
scripts/run_tsfm.sh cuda
echo GPU_ALL_DONE
