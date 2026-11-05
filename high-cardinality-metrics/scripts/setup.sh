#!/bin/bash
# setup.sh: prepare one run.
#   1. create the bucket if it does not exist
#   2. store the Coralogix keys in Secrets Manager (the boxes read them there)
#   3. package stage.sh, the generator source and the harness, upload them to
#      s3://$BENCH_BUCKET/$RUN_TAG/code/ (every box pulls its code from there)
#   4. terraform init
# Reads its settings from the environment (see .env.example).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AWS_REGION:?}" "${BENCH_BUCKET:?}" "${RUN_TAG:?}" "${CX_SECRET_ID:?}"
: "${CX_SEND_KEY:?}" "${CX_API_KEY:?}" "${CX_DOMAIN:?}"

if ! aws s3api head-bucket --bucket "$BENCH_BUCKET" 2>/dev/null; then
  echo "creating bucket $BENCH_BUCKET"
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BENCH_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BENCH_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
fi

SECRET=$(python3 -c 'import json,os;print(json.dumps({"send_key":os.environ["CX_SEND_KEY"],"api_key":os.environ["CX_API_KEY"],"domain":os.environ["CX_DOMAIN"]}))')
if aws secretsmanager describe-secret --secret-id "$CX_SECRET_ID" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$CX_SECRET_ID" --region "$AWS_REGION" \
    --secret-string "$SECRET" >/dev/null
else
  aws secretsmanager create-secret --name "$CX_SECRET_ID" --region "$AWS_REGION" \
    --secret-string "$SECRET" >/dev/null
fi
echo "secret $CX_SECRET_ID stored"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
tar czf "$TMP/generator.tgz" --exclude metricgen generator
tar czf "$TMP/harness.tgz" --exclude __pycache__ harness
aws s3 cp infra/stage.sh "s3://$BENCH_BUCKET/$RUN_TAG/code/stage.sh" --region "$AWS_REGION" >/dev/null
aws s3 cp "$TMP/generator.tgz" "s3://$BENCH_BUCKET/$RUN_TAG/code/generator.tgz" --region "$AWS_REGION" >/dev/null
aws s3 cp "$TMP/harness.tgz" "s3://$BENCH_BUCKET/$RUN_TAG/code/harness.tgz" --region "$AWS_REGION" >/dev/null
echo "code uploaded to s3://$BENCH_BUCKET/$RUN_TAG/code/"

terraform -chdir=infra init -input=false
