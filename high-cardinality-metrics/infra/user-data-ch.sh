#!/bin/bash
# ClickHouse box bootstrap. ROLE is ch-loader or ch-server.
# BUCKET, REGION and RUN_TAG come from /etc/bench.env (written by infra/main.tf).
# Both pin the SAME ClickHouse version (26.10.2.103) because ATTACH requires the
# loader and server builds to match. The loader additionally runs an otel collector
# (OTLP to ClickHouse) and the metricgen binary; the server is read-only.
set -uxo pipefail
ROLE="__ROLE__"; IDX="__IDX__"; SCALE="__SCALE__"; REUSE="__REUSE__"
B=$BUCKET
CHV=26.10.2.103
OTELCOL_VERSION=0.164.0

dnf -y install jq tar gzip >/dev/null 2>&1

# ------------------------------------------------------------- ClickHouse (pinned)
mkdir -p /opt/staging && cd /opt/staging
base="https://github.com/ClickHouse/ClickHouse/releases/download/v${CHV}-stable"
for pkg in clickhouse-common-static clickhouse-server clickhouse-client; do
  curl -fsSL -o $pkg.rpm "$base/${pkg}-${CHV}.aarch64.rpm"
done
rpm -Uvh --quiet clickhouse-common-static.rpm clickhouse-server.rpm clickhouse-client.rpm
mkdir -p /etc/clickhouse-server/config.d
cat > /etc/clickhouse-server/config.d/listen.xml <<'XML'
<clickhouse><listen_host>0.0.0.0</listen_host></clickhouse>
XML

# Stock ClickHouse server settings. The load rate is set on the generator side
# (--backfill-min-tick 6s in stage.sh).
systemctl enable --now clickhouse-server
sleep 20
clickhouse-client --query "CREATE DATABASE IF NOT EXISTS otel" || true

# ------------------------------------------------------------- loader-only extras
if [ "$ROLE" = "ch-loader" ]; then
  cd /tmp
  curl -fsSL -o otelcol.tgz \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTELCOL_VERSION}/otelcol-contrib_${OTELCOL_VERSION}_linux_arm64.tar.gz"
  mkdir -p /opt/otelcol && tar xzf otelcol.tgz -C /opt/otelcol
  install -m0755 /opt/otelcol/otelcol-contrib /usr/local/bin/otelcol-contrib
  mkdir -p /etc/otelcol
  cat > /etc/otelcol/config.yaml <<'YAML'
receivers:
  otlp: { protocols: { http: { endpoint: 0.0.0.0:4318, max_request_body_size: 67108864 } } }
exporters:
  clickhouse:
    endpoint: tcp://127.0.0.1:9000?dial_timeout=10s
    database: otel
    ttl: 0
    create_schema: true
    sending_queue:
      enabled: true
      block_on_overflow: true
      queue_size: 5000
      num_consumers: 8
      batch: { min_size: 10000, max_size: 50000, flush_timeout: 5s }
service:
  telemetry: { metrics: { level: basic } }
  pipelines: { metrics: { receivers: [otlp], exporters: [clickhouse] } }
YAML
  cat > /etc/systemd/system/otelcol.service <<'UNIT'
[Unit]
Description=otelcol-contrib to ClickHouse
After=network-online.target clickhouse-server.service
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/otelcol-contrib --config=/etc/otelcol/config.yaml
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload && systemctl enable --now otelcol
  # build the generator
  mkdir -p /opt/bench/go /opt/bench/gocache && chmod -R 777 /opt/bench
  cd /tmp && curl -fsSL -o go.tgz https://go.dev/dl/go1.27.3.linux-arm64.tar.gz
  rm -rf /usr/local/go && tar -C /usr/local -xzf go.tgz
fi

# ------------------------------------------------------------- code + stage role
mkdir -p /opt/bench && chmod -R 777 /opt/bench && cd /opt/bench
aws s3 cp s3://$B/$RUN_TAG/code/stage.sh /opt/stage.sh --region $REGION && chmod +x /opt/stage.sh
if [ "$ROLE" = "ch-loader" ]; then
  aws s3 cp s3://$B/$RUN_TAG/code/generator.tgz /opt/bench/generator.tgz --region $REGION && tar xzf generator.tgz
  export HOME=/root GOPATH=/opt/bench/go GOMODCACHE=/opt/bench/go/pkg/mod GOCACHE=/opt/bench/gocache
  cd /opt/bench/generator && /usr/local/go/bin/go build -o /opt/bench/metricgen ./cmd/metricgen
fi
echo READY > /var/log/bench-ready.txt
nohup setsid /opt/stage.sh "$ROLE" "$IDX" "$SCALE" "$REUSE" >/var/log/stage-boot.log 2>&1 &
