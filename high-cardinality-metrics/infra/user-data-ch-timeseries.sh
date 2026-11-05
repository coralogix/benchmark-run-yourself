#!/bin/bash
# ClickHouse TimeSeries box bootstrap. ROLE is ts-server.
# BUCKET, REGION and RUN_TAG come from /etc/bench.env (written by infra/main.tf).
# Installs the same ClickHouse build as the MergeTree boxes (26.10.2.103) and
# changes three things from stock, all needed to create and query a TimeSeries
# table:
#   users.d   allow_experimental_time_series_table=1 and enable_time_series_table=1,
#             without which CREATE TABLE ... ENGINE = TimeSeries is rejected
#   config.d  custom_settings_prefixes = stats,SQL_, without which ClickHouse
#             rejects the harness's stats=all URL parameter as an unknown setting
#   config.d  the prometheus_api_v1 HTTP handler for URLs under /prometheus/api/v1 on
#             :8123, reading prometheus.metrics, after the default handlers
# No async_insert or other server tuning. The box also builds metricgen, which
# loads the week into the table through clickhouse-client (infra/stage.sh, role
# ts-server).
set -uxo pipefail
ROLE="__ROLE__"; IDX="__IDX__"; SCALE="__SCALE__"; REUSE="__REUSE__"
B=$BUCKET
CHV=26.10.2.103

dnf -y install jq tar gzip >/dev/null 2>&1

# ------------------------------------------------------------- ClickHouse (pinned)
mkdir -p /opt/staging && cd /opt/staging
base="https://github.com/ClickHouse/ClickHouse/releases/download/v${CHV}-stable"
for pkg in clickhouse-common-static clickhouse-server clickhouse-client; do
  curl -fsSL -o $pkg.rpm "$base/${pkg}-${CHV}.aarch64.rpm"
done
rpm -Uvh --quiet clickhouse-common-static.rpm clickhouse-server.rpm clickhouse-client.rpm
mkdir -p /etc/clickhouse-server/config.d /etc/clickhouse-server/users.d
cat > /etc/clickhouse-server/config.d/listen.xml <<'XML'
<clickhouse><listen_host>0.0.0.0</listen_host></clickhouse>
XML
cat > /etc/clickhouse-server/config.d/custom-prefix.xml <<'XML'
<clickhouse><custom_settings_prefixes>stats,SQL_</custom_settings_prefixes></clickhouse>
XML
cat > /etc/clickhouse-server/config.d/prometheus.xml <<'XML'
<clickhouse><http_handlers>
 <defaults/>
 <rule><url_prefix>/prometheus/api/v1</url_prefix>
  <handler><type>prometheus_api_v1</type><database>prometheus</database><table>metrics</table></handler>
 </rule>
</http_handlers></clickhouse>
XML
cat > /etc/clickhouse-server/users.d/ts.xml <<'XML'
<clickhouse>
  <profiles>
    <default>
      <allow_experimental_time_series_table>1</allow_experimental_time_series_table>
      <enable_time_series_table>1</enable_time_series_table>
    </default>
  </profiles>
</clickhouse>
XML

systemctl enable --now clickhouse-server
sleep 20

# ------------------------------------------------------------- generator
mkdir -p /opt/bench/go /opt/bench/gocache && chmod -R 777 /opt/bench
cd /tmp && curl -fsSL -o go.tgz https://go.dev/dl/go1.27.3.linux-arm64.tar.gz
rm -rf /usr/local/go && tar -C /usr/local -xzf go.tgz

# ------------------------------------------------------------- code + stage role
cd /opt/bench
aws s3 cp s3://$B/$RUN_TAG/code/stage.sh /opt/stage.sh --region $REGION && chmod +x /opt/stage.sh
aws s3 cp s3://$B/$RUN_TAG/code/generator.tgz /opt/bench/generator.tgz --region $REGION && tar xzf generator.tgz
export HOME=/root GOPATH=/opt/bench/go GOMODCACHE=/opt/bench/go/pkg/mod GOCACHE=/opt/bench/gocache
cd /opt/bench/generator && /usr/local/go/bin/go build -o /opt/bench/metricgen ./cmd/metricgen
echo READY > /var/log/bench-ready.txt
nohup setsid /opt/stage.sh "$ROLE" "$IDX" "$SCALE" "$REUSE" >/var/log/stage-boot.log 2>&1 &
