#!/bin/bash
# clean.sh: destroy every resource Terraform created. The bucket, the corpus
# under s3://$BENCH_BUCKET/corpus/ and the Secrets Manager secret are kept so
# a later run can reuse them (REUSE=1); delete them by hand when finished.
set -euo pipefail
cd "$(dirname "$0")/.."
terraform -chdir=infra destroy -input=false -auto-approve -var scale="${SCALE:-test}" -var reuse="${REUSE:-0}"
