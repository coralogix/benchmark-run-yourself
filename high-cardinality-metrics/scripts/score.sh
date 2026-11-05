#!/bin/bash
# score.sh: compute the published figures from results/.
#   results/summary-<engine>.txt  per-cell tables from harness/summarise.py
#   results/page-data.json        every cell, the Coralogix network floor (the fastest
#                                 Coralogix request in the run), engine time and the cell winners
# The ClickHouse TimeSeries sweep is included when results/sweep-clickhouse-timeseries/ exists.
set -euo pipefail
cd "$(dirname "$0")/.."
ARGS=()
add(){ [ -d "results/$3" ] && ARGS+=(--engine "$1=$2=results/$3") || echo "skip $1: results/$3 missing"; }
add coralogix "Coralogix" sweep-coralogix
add prometheus "Prometheus" sweep-prometheus
add clickhouse "ClickHouse (MergeTree)" sweep-clickhouse
add clickhouse_timeseries "ClickHouse (TimeSeries)" sweep-clickhouse-timeseries
add mimir "Grafana Mimir" sweep-mimir
for d in results/sweep-*/; do
  e=$(basename "$d"); e=${e#sweep-}
  python3 harness/summarise.py "$d" --phase concurrent > "results/summary-$e.txt"
  echo "wrote results/summary-$e.txt"
done
python3 analysis/build_page_data.py "${ARGS[@]}" --out results/page-data.json
python3 analysis/figures.py results/page-data.json > results/figures.txt && echo "wrote results/figures.txt"
