#!/bin/bash
# The CPU work, in three parts:
#   naive   the naive baselines over every cutoff, one process (bench_production.py naive)
#   stats   the classical models scored, one process per cutoff, PARALLEL processes at once (default 32: every cutoff
#           at once); written to results/production/stats_parts/
#   timing  the separate timing run, one forecast at a time on this one machine: the eight foundation models on CPU
#           (scripts/run_tsfm.sh cpu, to results/production/tsfm_cpu/), then the classical models one cutoff after
#           another (to results/production/timing_stats/). Every speed on the page comes from this part, so run it on
#           one machine with nothing else running.
# Usage: scripts/run_cpu.sh [naive|stats|timing ...]   (default: all three, in that order)
set -eu
cd "$(dirname "$0")/.."
PY=envs/timestral/bin/python
parts=${*:-naive stats timing}
echo "machine: $(uname -m) $(sysctl -n machdep.cpu.brand_string 2>/dev/null || grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)" \
     "memory: $(sysctl -n hw.memsize 2>/dev/null || grep MemTotal /proc/meminfo)"
for p in $parts; do
  case $p in
    naive)  $PY -W ignore scripts/bench_production.py naive ;;
    stats)  $PY scripts/bench_production.py cutoffs | xargs -P "${PARALLEL:-32}" -L 1 $PY -W ignore scripts/bench_production.py stats ;;
    timing) scripts/run_tsfm.sh cpu
            $PY scripts/bench_production.py cutoffs | STATS_OUT=timing_stats xargs -P 1 -L 1 $PY -W ignore scripts/bench_production.py stats ;;
    *) echo "unknown part $p"; exit 1 ;;
  esac
done
