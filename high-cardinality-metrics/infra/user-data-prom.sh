#!/bin/bash
# Prometheus box bootstrap. ROLE is pm-loader or prom-server.
# BUCKET, REGION and RUN_TAG come from /etc/bench.env (written by infra/main.tf).
# pm-loader runs the OTLP write receiver (metricgen pushes the week in), then hands
# its TSDB blocks to prom-server (via S3) and to the Mimir cluster (via mimirtool).
# prom-server serves the downloaded blocks read-only.
set -uxo pipefail
ROLE="__ROLE__"; IDX="__IDX__"; SCALE="__SCALE__"; REUSE="__REUSE__"
B=$BUCKET
PROMV=3.1.0
MIMIRV=2.14.3

dnf -y install jq tar gzip >/dev/null 2>&1
useradd --system --no-create-home --shell /sbin/nologin prometheus || true
mkdir -p /var/lib/prometheus /etc/prometheus
chown -R prometheus:prometheus /var/lib/prometheus

cd /tmp
curl -fsSL -o prom.tgz "https://github.com/prometheus/prometheus/releases/download/v${PROMV}/prometheus-${PROMV}.linux-arm64.tar.gz"
tar xzf prom.tgz
install -m0755 prometheus-${PROMV}.linux-arm64/prometheus /usr/local/bin/prometheus
install -m0755 prometheus-${PROMV}.linux-arm64/promtool  /usr/local/bin/promtool

cat > /etc/prometheus/prometheus.yml <<'YML'
global:
  scrape_interval: 1m
YML
chown -R prometheus:prometheus /etc/prometheus

# Both keep 30 days so the whole week survives. The loader also enables the OTLP
# write receiver (metricgen pushes the week in) and the admin API (stage.sh snapshots
# the TSDB for transfer).
EXTRA=""
[ "$ROLE" = "pm-loader" ] && EXTRA="--web.enable-otlp-receiver --web.enable-admin-api"
cat > /etc/systemd/system/prometheus.service <<UNIT
[Unit]
Description=Prometheus
After=network-online.target
Wants=network-online.target
[Service]
User=prometheus
Group=prometheus
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time=30d \
  --web.listen-address=0.0.0.0:9090 $EXTRA
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now prometheus
sleep 10

# ------------------------------------------------------------- loader-only extras
if [ "$ROLE" = "pm-loader" ]; then
  curl -fsSL -o /usr/local/bin/mimirtool "https://github.com/grafana/mimir/releases/download/mimir-${MIMIRV}/mimirtool-linux-arm64"
  chmod +x /usr/local/bin/mimirtool
  mkdir -p /opt/bench/go /opt/bench/gocache && chmod -R 777 /opt/bench
  cd /tmp && curl -fsSL -o go.tgz https://go.dev/dl/go1.27.3.linux-arm64.tar.gz
  rm -rf /usr/local/go && tar -C /usr/local -xzf go.tgz
fi

# ------------------------------------------------------------- code + stage role
mkdir -p /opt/bench && chmod -R 777 /opt/bench && cd /opt/bench
aws s3 cp s3://$B/$RUN_TAG/code/stage.sh /opt/stage.sh --region $REGION && chmod +x /opt/stage.sh
if [ "$ROLE" = "pm-loader" ]; then
  aws s3 cp s3://$B/$RUN_TAG/code/generator.tgz /opt/bench/generator.tgz --region $REGION && tar xzf generator.tgz
  export HOME=/root GOPATH=/opt/bench/go GOMODCACHE=/opt/bench/go/pkg/mod GOCACHE=/opt/bench/gocache
  cd /opt/bench/generator && /usr/local/go/bin/go build -o /opt/bench/metricgen ./cmd/metricgen
fi
echo READY > /var/log/bench-ready.txt
nohup setsid /opt/stage.sh "$ROLE" "$IDX" "$SCALE" "$REUSE" >/var/log/stage-boot.log 2>&1 &
