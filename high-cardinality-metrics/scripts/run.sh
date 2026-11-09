#!/bin/bash
# run.sh: provision the fleet and wait for it to finish.
# The boxes drive themselves (infra/stage.sh): load, verify, sweep every
# concurrency level, upload the results, then the coordinator terminates the
# fleet. This script applies the Terraform, waits for the 06-data-safe marker,
# and downloads one result tarball per engine into results/.
#   SCALE=test  a small rehearsal (band b1, 20 minutes of data, concurrency 1 and 10)
#   SCALE=real  the full run (b1 to b4, one week, concurrency 1 to 100)
#   REUSE=1     mount the stored corpus; ClickHouse TimeSeries has none and is left out
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AWS_REGION:?}" "${BENCH_BUCKET:?}" "${RUN_TAG:?}"
SCALE="${SCALE:-test}"
# Every run gets its own prefix, so markers, results and Mimir blocks never carry over between runs.
BASE_TAG="$RUN_TAG"
RUN_TAG="$BASE_TAG-$(date -u +%Y%m%dt%H%M%Sz)"
export RUN_TAG TF_VAR_run_tag="$RUN_TAG"
aws s3 sync "s3://$BENCH_BUCKET/$BASE_TAG/code/" "s3://$BENCH_BUCKET/$RUN_TAG/code/" --region "$AWS_REGION" >/dev/null
mkdir -p results
echo "$RUN_TAG" > results/run-tag
echo "run tag: $RUN_TAG"
terraform -chdir=infra apply -input=false -auto-approve -var scale="$SCALE" -var reuse="${REUSE:-0}"
WAIT_MAX="${WAIT_MAX:-172800}" scripts/wait-marker.sh 06-data-safe
mkdir -p results
ENGINES="coralogix mimir prometheus clickhouse victoriametrics"
[ "${REUSE:-0}" = "1" ] || ENGINES="$ENGINES clickhouse-timeseries"
for e in $ENGINES; do
  aws s3 cp "s3://$BENCH_BUCKET/$RUN_TAG/sweep-$e.tgz" "results/sweep-$e.tgz" --region "$AWS_REGION"
  tar xzf "results/sweep-$e.tgz" -C results
done
echo "results in results/sweep-<engine>/"
