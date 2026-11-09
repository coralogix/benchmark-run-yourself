#!/bin/bash
# VictoriaMetrics box bootstrap. ROLE is vm-server.
# BUCKET, REGION and RUN_TAG come from /etc/bench.env (written by infra/main.tf).
# Installs single-node VictoriaMetrics and vmctl, and starts it empty with the
# import flags so the coordinator sees it up. stage.sh (role vm-server) imports the
# Prometheus loader's blocks from S3, then restarts it with the tuned query flags.
set -uxo pipefail
ROLE="__ROLE__"; IDX="__IDX__"; SCALE="__SCALE__"; REUSE="__REUSE__"
B=$BUCKET
VMV=v1.155.0

dnf -y install jq tar gzip python3 >/dev/null 2>&1
cd /tmp
curl -fsSL -o vm.tgz "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VMV}/victoria-metrics-linux-arm64-${VMV}.tar.gz"
curl -fsSL -o vmutils.tgz "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VMV}/vmutils-linux-arm64-${VMV}.tar.gz"
tar xzf vm.tgz && tar xzf vmutils.tgz
install -m0755 victoria-metrics-prod /usr/local/bin/victoria-metrics
install -m0755 vmctl-prod /usr/local/bin/vmctl
mkdir -p /var/lib/victoria-metrics

# The flags live in /etc/victoria-metrics.env so stage.sh can swap the import
# flags for the tuned query flags and restart. Import: long retention (the week must
# not age out) and the response cache off, as VictoriaMetrics advises for backfilling.
echo 'VMFLAGS="-storageDataPath=/var/lib/victoria-metrics -retentionPeriod=100y -httpListenAddr=:8428 -search.disableCache"' \
  > /etc/victoria-metrics.env
cat > /etc/systemd/system/victoria-metrics.service <<'UNIT'
[Unit]
Description=VictoriaMetrics
After=network-online.target
Wants=network-online.target
[Service]
EnvironmentFile=/etc/victoria-metrics.env
ExecStart=/usr/local/bin/victoria-metrics $VMFLAGS
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now victoria-metrics

# ------------------------------------------------------------- code + stage role
mkdir -p /opt/bench && chmod -R 777 /opt/bench && cd /opt/bench
aws s3 cp s3://$B/$RUN_TAG/code/stage.sh /opt/stage.sh --region $REGION && chmod +x /opt/stage.sh
echo READY > /var/log/bench-ready.txt
nohup setsid /opt/stage.sh "$ROLE" "$IDX" "$SCALE" "$REUSE" >/var/log/stage-boot.log 2>&1 &
