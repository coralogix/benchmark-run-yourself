#!/bin/bash
# Worker bootstrap: coordinator, cx-sender, harness. Pulls its own code from S3
# (uploaded by scripts/setup.sh), then starts its stage role.
# BUCKET, REGION and RUN_TAG come from /etc/bench.env (written by infra/main.tf).
set -uxo pipefail
ROLE="__ROLE__"; IDX="__IDX__"; SCALE="__SCALE__"; REUSE="__REUSE__"
B=$BUCKET
dnf -y install git jq tar gzip python3 >/dev/null 2>&1
mkdir -p /opt/bench/go /opt/bench/gocache && chmod -R 777 /opt/bench && cd /opt/bench

aws s3 cp s3://$B/$RUN_TAG/code/stage.sh /opt/stage.sh --region $REGION && chmod +x /opt/stage.sh
aws s3 cp s3://$B/$RUN_TAG/code/harness.tgz /opt/bench/harness.tgz --region $REGION && tar xzf harness.tgz

# cx-sender needs the generator binary; harness/coordinator do not
if [ "$ROLE" = "cx-sender" ]; then
  aws s3 cp s3://$B/$RUN_TAG/code/generator.tgz /opt/bench/generator.tgz --region $REGION && tar xzf generator.tgz
  cd /tmp && curl -fsSL -o go.tgz https://go.dev/dl/go1.27.3.linux-arm64.tar.gz
  rm -rf /usr/local/go && tar -C /usr/local -xzf go.tgz
  export HOME=/root GOPATH=/opt/bench/go GOMODCACHE=/opt/bench/go/pkg/mod GOCACHE=/opt/bench/gocache
  cd /opt/bench/generator && /usr/local/go/bin/go build -o /opt/bench/metricgen ./cmd/metricgen
fi
echo READY > /var/log/bench-ready.txt
nohup setsid /opt/stage.sh "$ROLE" "$IDX" "$SCALE" "$REUSE" >/var/log/stage-boot.log 2>&1 &
