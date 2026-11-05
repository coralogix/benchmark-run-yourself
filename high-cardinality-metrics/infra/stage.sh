#!/bin/bash
# stage.sh: self-driving orchestration for the self-hosted engines, Coralogix
# and the query clients (loader/serve split). Every box runs it with its role.
#
# The engines are loaded on FAT TRANSIENT boxes and the finished data is moved
# onto MATCHED-SPEND serving boxes, so only the serving boxes are ever measured.
# Ingest is NOT part of this benchmark; it measures query performance at matched spend over an identical week of data.
#   - ClickHouse : ch-loader ingests and merges, FREEZEs parts to S3, ch-server ATTACHes them
#   - Prometheus : pm-loader ingests, snapshots TSDB blocks to S3, prom-server serves them
#   - Mimir      : pm-loader backfills the same blocks into the serving cluster with mimirtool
#   - Coralogix  : cx-sender ingests the week straight to the SaaS endpoint
#   - ClickHouse TimeSeries : ts-server loads its own week with metricgen tsinsert
#                  (no S3 corpus; loaded fresh on every run, left out on REUSE)
#
# Every transfer rides S3, the same coordination bus as the markers. Boxes block on
# the marker they need and write the marker they own; nothing waits on a human.
#
# NO TTL. Teardown is the LAST STEP: the coordinator terminates the whole fleet
# only after every engine's result tarball is confirmed in S3.
#
#   markers (s3://$BUCKET/$RUN_TAG/):
#     01-infra-ready        coordinator : all serving engines' processes up
#     02-ch-parts-ready     ch-loader   : frozen ClickHouse parts in S3
#     02-pm-blocks-ready    pm-loader   : Prometheus TSDB blocks in S3
#     02-mimir-loaded       pm-loader   : blocks backfilled into the Mimir cluster
#     data-eval-time        coordinator : the query instant, unix seconds
#     02-cx-shard-<i>       cx-sender   : shard <i> sent to Coralogix
#     03-<eng>-data         server/role : <eng> is loaded and queryable
#     03-verify-<eng>       harness     : verify phase returned data
#     04-c<NNN>-<eng>       harness     : one concurrency level finished + uploaded
#     05-sweep-<eng>        harness     : all levels finished
#     05-all-sweeps-done    coordinator : every engine's sweep finished
#     06-data-safe          coordinator : every result tarball confirmed in S3 (make run waits for this)
#     06-torn-down          coordinator : fleet terminated
#     ERROR-<stage>         anyone      : abort, reason in the body
set -uo pipefail
ROLE="${1:?role}"; IDX="${2:-0}"; SCALE="${3:-test}"; REUSE="${4:-0}"

# BUCKET, REGION, RUN_TAG, CX_SECRET_ID, MIMIR_IPS, PROM_IP, CH_IP and TS_IP are written
# to /etc/bench.env by infra/main.tf.
. /etc/bench.env
B=$BUCKET                 # code + markers
P=$RUN_TAG
# PERSISTENT, reusable corpus. The loaded and compacted artifacts (ClickHouse
# parts, Prometheus/Mimir TSDB blocks) land under corpus/ and are NEVER deleted at
# teardown, so a later run can mount them (REUSE=1) and skip the ~17h ingest.
CORPUS=$BUCKET/corpus
LOG=/var/log/stage-$ROLE-$IDX.log
exec >>"$LOG" 2>&1

# ---- scale profile: test is a small end-to-end rehearsal, real is the week -----
if [ "$SCALE" = "real" ]; then
  BANDS=b1,b2,b3,b4 ; BACKFILL=168h ; LEVELS="1 10 20 30 40 50 60 70 80 90 100"
  MIMIR_N=9 ; CX_SHARDS=4
else
  BANDS=b1 ; BACKFILL=20m ; LEVELS="1 10"
  MIMIR_N=1 ; CX_SHARDS=1
fi
MIMIR_IPS=$(echo $MIMIR_IPS | cut -d' ' -f1-$MIMIR_N)

# Engines swept in this run. ClickHouse TimeSeries has no stored corpus, so a
# REUSE run leaves it out (infra/main.tf creates neither its server nor its client).
ENGINES="coralogix mimir prometheus clickhouse"
[ "$REUSE" = "1" ] || ENGINES="$ENGINES clickhouse-timeseries"

# Stable corpus key: bands, interval and span. Same inputs give the same key, so the corpus is reusable.
CORPUS_PREFIX="$(echo "$BANDS" | tr -d ,)-1m-$BACKFILL"
CH_CORPUS="s3://$CORPUS/$CORPUS_PREFIX/clickhouse"
BLK_CORPUS="s3://$CORPUS/$CORPUS_PREFIX/blocks"
MANIFEST="s3://$CORPUS/$CORPUS_PREFIX/manifest.json"

say(){ echo "[$(date -u +%FT%TZ)] $ROLE/$IDX/$SCALE $*"; }
mark(){ echo "$2" | aws s3 cp - "s3://$B/$P/$1" --region $REGION >/dev/null 2>&1 && say "MARK $1"; }
have(){ aws s3 ls "s3://$B/$P/$1" --region $REGION >/dev/null 2>&1; }
waitfor(){ local m=$1 n=0
  until have "$m"; do
    n=$((n+1))
    if have "ERROR-abort"; then say "aborting: ERROR-abort present"; exit 1; fi
    [ $((n%30)) -eq 0 ] && say "waiting for $m (${n}0s)"
    sleep 10
  done
  say "got $m"; }
die(){ say "FATAL $1"; echo "$1" | aws s3 cp - "s3://$B/$P/ERROR-$2" --region $REGION >/dev/null 2>&1
       # also raise the global abort so every other role's waitfor bails instead
       # of hanging forever on a marker that will never appear
       echo "$2: $1" | aws s3 cp - "s3://$B/$P/ERROR-abort" --region $REGION >/dev/null 2>&1; exit 1; }
cxsecret(){ aws secretsmanager get-secret-value --secret-id "$CX_SECRET_ID" \
  --region $REGION --query SecretString --output text | python3 -c "import json,sys;print(json.load(sys.stdin)['$1'])"; }
# transient boxes (loaders, senders) self-terminate once their data is safely in
# S3, so a 16xlarge loader does not idle at ~$2.9/h through the multi-hour sweep.
# Called only after the handoff marker is written.
selfterm(){ local tok iid
  tok=$(curl -s -X PUT 'http://169.254.169.254/latest/api/token' -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
  iid=$(curl -s -H "X-aws-ec2-metadata-token: $tok" http://169.254.169.254/latest/meta-data/instance-id)
  say "self-terminating $iid ($1 done)"
  aws ec2 terminate-instances --region $REGION --instance-ids "$iid" >/dev/null 2>&1; }

MIMIR0=$(echo $MIMIR_IPS | cut -d' ' -f1)

case "$ROLE" in

# ===========================================================================
coordinator)
  say "waiting for serving engines to come up ($MIMIR_N mimir, prom, clickhouse, timeseries)"
  for t in $(seq 1 120); do
    ok=0; for i in $MIMIR_IPS; do curl -sf -m5 "http://$i:8080/ready" >/dev/null 2>&1 && ok=$((ok+1)); done
    pok=0; curl -sf -m5 "http://$PROM_IP:9090/-/ready" >/dev/null 2>&1 && pok=1
    chok=0; curl -sf -m5 "http://$CH_IP:8123/?query=SELECT%201" >/dev/null 2>&1 && chok=1
    tsok=1
    if [ "$REUSE" != "1" ]; then
      tsok=0; curl -sf -m5 "http://$TS_IP:8123/?query=SELECT%201" >/dev/null 2>&1 && tsok=1
    fi
    say "mimir $ok/$MIMIR_N prom $pok clickhouse $chok timeseries $tsok"
    [ "$ok" -ge "$MIMIR_N" ] && [ "$pok" = 1 ] && [ "$chok" = 1 ] && [ "$tsok" = 1 ] && break
    sleep 20
  done
  [ "$ok" -ge "$MIMIR_N" ] && [ "$pok" = 1 ] && [ "$chok" = 1 ] && [ "$tsok" = 1 ] \
    || die "engines not ready: mimir $ok/$MIMIR_N prom $pok ch $chok ts $tsok" infra
  mark 01-infra-ready "mimir $MIMIR_N/$MIMIR_N, prom up, clickhouse up, timeseries $tsok"
  # Pin query evaluation inside the data window. The data is historical and there is
  # no live ingestion, so wall-clock now() would evaluate on an empty recent window.
  # Every harness role reads this and queries at it, so every engine is compared
  # at the same instant regardless of when each concurrency level runs.
  #
  # On REUSE the data keeps the timestamps it was loaded with, so the instant comes
  # from the corpus manifest; now() would fall outside the stored week.
  if [ "$REUSE" = "1" ]; then
    EVAL=$(aws s3 cp "$MANIFEST" - --region $REGION 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["eval_time"])' 2>/dev/null)
    [ -n "$EVAL" ] || die "REUSE but no eval_time in manifest $MANIFEST" manifest
    say "REUSE: eval time from manifest = $EVAL"
  else
    EVAL=$(( $(date -u +%s) - 300 ))
    # record the corpus manifest so a future REUSE run can mount this exact dataset
    printf '{"prefix":"%s","bands":"%s","interval":"1m","span":"%s","eval_time":%s,"created":"%s","clickhouse":"%s","blocks":"%s"}\n' \
      "$CORPUS_PREFIX" "$BANDS" "$BACKFILL" "$EVAL" "$(date -u +%FT%TZ)" "$CH_CORPUS" "$BLK_CORPUS" \
      | aws s3 cp - "$MANIFEST" --region $REGION >/dev/null && say "wrote corpus manifest $MANIFEST"
  fi
  mark data-eval-time "$EVAL"

  for e in $ENGINES; do waitfor "05-sweep-$e"; done
  mark 05-all-sweeps-done "all engines finished"
  say "final upload check"
  for e in $ENGINES; do
    aws s3 ls "s3://$B/$P/sweep-$e.tgz" --region $REGION || die "missing sweep-$e.tgz" upload
  done
  mark 06-data-safe "tarballs present in S3: $ENGINES"
  say "TEARDOWN: terminating every instance tagged Run=$P"
  IDS=$(aws ec2 describe-instances --region $REGION \
        --filters "Name=tag:Run,Values=$P" "Name=instance-state-name,Values=running,pending" \
        --query 'Reservations[].Instances[].InstanceId' --output text)
  mark 06-torn-down "$IDS"
  aws ec2 terminate-instances --region $REGION --instance-ids $IDS >/dev/null 2>&1
  ;;

# ===========================================================================
ch-loader)
  waitfor 01-infra-ready
  # REUSE: the parts already live in the persistent corpus; nothing to generate.
  if [ "$REUSE" = "1" ]; then
    aws s3 ls "$CH_CORPUS/" --region $REGION >/dev/null 2>&1 || die "REUSE but no ClickHouse corpus at $CH_CORPUS" ch-reuse
    say "REUSE: ClickHouse parts already in corpus, skipping ingest"
    mark 02-ch-parts-ready "reused corpus"
    selfterm "ch-loader"; exit 0
  fi
  cd /opt/bench
  # Stock ClickHouse. The generator spends at least 6 s on each 1.8M-sample tick
  # (--backfill-min-tick 6s).
  say "paced backfill $BACKFILL ($BANDS) to local ClickHouse via otelcol (6s/tick)"
  clickhouse-client --query "SELECT 1" >/dev/null 2>&1 || die "local CH down" ch-loader
  ./metricgen bench --targets "clickhouse=http://127.0.0.1:4318/v1/metrics" --backfill-min-tick 6s \
    --bands "$BANDS" --shard 0 --shards 1 --backfill "$BACKFILL" --interval 1m --seed 1 \
    --out /opt/bench/results/chload || die "ch backfill failed" backfill-clickhouse
  say "backfill done; waiting 60s for otelcol queue to drain then flushing"
  sleep 60
  for t in $(clickhouse-client --query "SELECT name FROM system.tables WHERE database='otel' AND name LIKE 'otel_metrics_%' FORMAT TSV"); do
    clickhouse-client --query "OPTIMIZE TABLE otel.$t FINAL" 2>/dev/null || true
  done
  say "FREEZE, upload parts and DDL to the persistent corpus"
  rm -rf /var/lib/clickhouse/shadow/bench_* 2>/dev/null || true
  aws s3 rm "$CH_CORPUS/" --recursive --region $REGION >/dev/null 2>&1 || true   # clear only THIS prefix for a clean (re)generation
  TABLES=$(clickhouse-client --query "SELECT name FROM system.tables WHERE database='otel' AND name LIKE 'otel_metrics_%' FORMAT TSV")
  FROZE=0
  for t in $TABLES; do
    cnt=$(clickhouse-client --query "SELECT count() FROM otel.$t")
    [ "$cnt" -eq 0 ] && { say "skip empty $t"; continue; }
    FROZE=$((FROZE+1))
    clickhouse-client --query "ALTER TABLE otel.$t FREEZE WITH NAME 'bench_$t'"
    clickhouse-client --query "SHOW CREATE TABLE otel.$t" --format TSVRaw \
      | sed "s/ UUID '[^']*'//" > /tmp/$t.ddl
    aws s3 cp /tmp/$t.ddl "$CH_CORPUS/$t.ddl" --region $REGION >/dev/null
    find /var/lib/clickhouse/shadow/bench_$t -type f -name count.txt -printf '%h\n' | while read d; do
      pn=$(basename "$d")
      tar czf /tmp/$t.$pn.tar.gz -C "$(dirname "$d")" "$pn"
      aws s3 cp /tmp/$t.$pn.tar.gz "$CH_CORPUS/parts/$t/$pn.tar.gz" --region $REGION >/dev/null
    done
    say "froze $t: $cnt rows"
  done
  [ "$FROZE" -gt 0 ] || die "no non-empty ClickHouse tables to freeze" ch-parts
  mark 02-ch-parts-ready "froze $FROZE tables"
  selfterm "ch-loader"
  ;;

# ===========================================================================
ch-server)
  # install done by user-data; create empty tables from DDL and ATTACH the parts
  waitfor 02-ch-parts-ready
  clickhouse-client --query "CREATE DATABASE IF NOT EXISTS otel"
  aws s3 cp "$CH_CORPUS/" /tmp/chddl/ --recursive --exclude "parts/*" --region $REGION >/dev/null
  for ddl in /tmp/chddl/*.ddl; do
    [ -f "$ddl" ] || continue
    t=$(basename "$ddl" .ddl)
    clickhouse-client --multiquery < "$ddl" || die "create $t failed" ch-server
    DST=$(clickhouse-client --query "SELECT data_paths[1] FROM system.tables WHERE database='otel' AND name='$t'")
    mkdir -p "$DST/detached"
    # pull every part tar for this table from the corpus, extract into detached, attach
    for key in $(aws s3 ls "$CH_CORPUS/parts/$t/" --region $REGION | awk '{print $4}'); do
      aws s3 cp "$CH_CORPUS/parts/$t/$key" /tmp/$key --region $REGION >/dev/null
      tar xzf /tmp/$key -C "$DST/detached/"
      pn=$(basename "$key" .tar.gz)
      chown -R clickhouse:clickhouse "$DST/detached/$pn"
      clickhouse-client --query "ALTER TABLE otel.$t ATTACH PART '$pn'" || die "attach $t/$pn" ch-server
    done
    rc=$(clickhouse-client --query "SELECT count() FROM otel.$t")
    say "attached $t: $rc rows"
  done
  mark 03-clickhouse-data "attached"
  ;;

# ===========================================================================
ts-server)
  # ClickHouse TimeSeries: the serving node loads its own week. tsinsert writes
  # the same series, labels and values as the other loaders, one row per series
  # with its whole sample array: INSERT INTO prometheus.metrics (metric_name,
  # tags, samples) FORMAT JSONEachRow through clickhouse-client, 4,000 series per
  # INSERT, 24 inserts at once. Loading starts after 01-infra-ready, like every
  # other loader, so the pinned query instant lies inside the loaded week.
  [ "$REUSE" = "1" ] && die "REUSE does not cover ClickHouse TimeSeries; run without REUSE" ts-reuse
  waitfor 01-infra-ready
  clickhouse-client --query "CREATE DATABASE IF NOT EXISTS prometheus" || die "create database failed" ts-server
  clickhouse-client --query "CREATE TABLE IF NOT EXISTS prometheus.metrics ENGINE = TimeSeries" \
    || die "create TimeSeries table failed" ts-server
  cd /opt/bench
  say "tsinsert $BACKFILL ($BANDS) into prometheus.metrics"
  ./metricgen tsinsert --bands "$BANDS" --backfill "$BACKFILL" --interval 1m --seed 1 \
    --table prometheus.metrics --chunk 4000 --parallel 24 || die "tsinsert failed" backfill-timeseries
  waitfor data-eval-time
  EVAL=$(aws s3 cp "s3://$B/$P/data-eval-time" - --region $REGION 2>/dev/null | tr -d '[:space:]')
  FB=$(echo "$BANDS" | cut -d, -f1)
  # confirm the PromQL endpoint the harness uses returns data at the query instant
  R=$(curl -s -G "http://127.0.0.1:8123/prometheus/api/v1/query" \
        --data-urlencode "query=count(kube_pod_info_${FB})" --data-urlencode "time=$EVAL" 2>/dev/null)
  echo "$R" | grep -q '"value"' || die "TimeSeries PromQL endpoint returned no data at $EVAL: $R" ts-serve
  say "timeseries serving: $R"
  mark 03-clickhouse-timeseries-data "loaded"
  ;;

# ===========================================================================
pm-loader)
  waitfor 01-infra-ready
  cd /opt/bench
  if [ "$REUSE" = "1" ]; then
    # blocks already in the corpus; prom-server reads them directly. Mimir still
    # needs them backfilled into THIS (fresh) cluster, so pull + mimirtool.
    aws s3 ls "$BLK_CORPUS/" --region $REGION >/dev/null 2>&1 || die "REUSE but no block corpus at $BLK_CORPUS" pm-reuse
    say "REUSE: Prometheus blocks already in corpus; backfilling Mimir from corpus"
    mkdir -p /tmp/reuseblocks && aws s3 sync "$BLK_CORPUS/" /tmp/reuseblocks/ --region $REGION >/dev/null
    mark 02-pm-blocks-ready "reused corpus"
    UP=0
    for d in /tmp/reuseblocks/*/; do
      [ -f "$d/meta.json" ] || continue
      mimirtool backfill "$d" --address="http://$MIMIR0:8080" --id=anonymous 2>&1 | tail -1; UP=$((UP+1))
    done
    say "mimir backfill (reuse) attempted $UP blocks"
    mark 02-mimir-loaded "reused $UP blocks"
    selfterm "pm-loader"; exit 0
  fi
  say "ingest $BACKFILL ($BANDS) to local Prometheus (OTLP receiver)"
  curl -sf -m5 "http://127.0.0.1:9090/-/ready" >/dev/null 2>&1 || die "local prom down" pm-loader
  ./metricgen bench --targets "prometheus=http://127.0.0.1:9090/api/v1/otlp/v1/metrics" \
    --bands "$BANDS" --shard 0 --shards 1 --backfill "$BACKFILL" --interval 1m --seed 1 \
    --out /opt/bench/results/pmload || die "prom backfill failed" backfill-prometheus
  say "flushing head to blocks (snapshot) and compacting"
  sleep 30
  # snapshot writes the head to an on-disk block so the whole window is in blocks
  curl -s -XPOST "http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot?skip_head=false" || true
  sleep 20
  SNAP=$(ls -dt /var/lib/prometheus/snapshots/* 2>/dev/null | head -1)
  SRC=${SNAP:-/var/lib/prometheus}
  say "block source: $SRC"
  aws s3 rm "$BLK_CORPUS/" --recursive --region $REGION >/dev/null 2>&1 || true   # clear only THIS prefix for a clean (re)generation
  # upload every block dir (has meta.json) to the persistent corpus
  BLOCKS=0
  for d in "$SRC"/*/; do
    [ -f "$d/meta.json" ] || continue
    bn=$(basename "$d")
    aws s3 sync "$d" "$BLK_CORPUS/$bn/" --region $REGION >/dev/null
    BLOCKS=$((BLOCKS+1))
  done
  say "uploaded $BLOCKS prometheus blocks to corpus"
  [ "$BLOCKS" -gt 0 ] || die "snapshot produced no prometheus blocks" pm-blocks
  mark 02-pm-blocks-ready "$BLOCKS blocks"
  # Mimir: backfill the SAME blocks into the serving cluster via its block-upload API
  say "mimirtool backfill to the Mimir cluster at $MIMIR0"
  UP=0
  for d in "$SRC"/*/; do
    [ -f "$d/meta.json" ] || continue
    mimirtool backfill "$d" --address="http://$MIMIR0:8080" --id=anonymous 2>&1 | tail -1
    UP=$((UP+1))
  done
  say "mimir backfill attempted $UP blocks"
  mark 02-mimir-loaded "$UP blocks"
  selfterm "pm-loader"
  ;;

# ===========================================================================
prom-server)
  waitfor 02-pm-blocks-ready
  systemctl stop prometheus 2>/dev/null || true
  rm -rf /var/lib/prometheus/* 2>/dev/null || true
  aws s3 sync "$BLK_CORPUS/" /var/lib/prometheus/ --region $REGION >/dev/null
  chown -R prometheus:prometheus /var/lib/prometheus 2>/dev/null || true
  systemctl start prometheus
  sleep 15
  curl -sf -m5 "http://127.0.0.1:9090/-/ready" >/dev/null 2>&1 || die "prom-server not ready after load" prom-server
  mark 03-prometheus-data "blocks served"
  ;;

# ===========================================================================
mimir)
  # cluster is started by user-data. idx0 confirms the backfilled blocks are
  # actually loaded by the store-gateway before declaring the engine ready.
  if [ "$IDX" = "0" ]; then
    waitfor 02-mimir-loaded
    say "waiting for store-gateway to load blocks + build bucket-index"
    FB=$(echo "$BANDS" | cut -d, -f1)
    ok=0
    for t in $(seq 1 120); do
      # range query over the whole recent window (not an instant-at-now), using the
      # Prometheus-style counter name the backfilled blocks actually carry. The
      # compactor builds the bucket-index on its cleanup cycle, so this can take
      # a few minutes on a cold tenant.
      R=$(curl -s -G -H "X-Scope-OrgID: anonymous" "http://127.0.0.1:8080/prometheus/api/v1/query" \
            --data-urlencode "query=count(count_over_time(http_requests_${FB}_total[24h]))" 2>/dev/null)
      if echo "$R" | grep -q '"value"'; then say "mimir serving blocks: $R"; ok=1; break; fi
      sleep 20
    done
    [ "$ok" = 1 ] || die "mimir store-gateway never served the backfilled blocks" mimir-serve
    mark 03-mimir-data "store-gateway serving"
  fi
  ;;

# ===========================================================================
cx-sender)
  waitfor 01-infra-ready
  if [ "$REUSE" = "1" ]; then
    # Coralogix is SaaS and retains the corpus on the account, so there is
    # nothing to re-ingest. (If account retention has aged the week out, run
    # without REUSE.)
    say "REUSE: Coralogix retains the corpus on-account; skipping ingest"
    [ "$IDX" = "0" ] && mark 03-coralogix-data "reused (on-account retention)"
    selfterm "cx-sender-$IDX"; exit 0
  fi
  cd /opt/bench
  export CORALOGIX_KEY=$(cxsecret send_key)
  DOMAIN=$(cxsecret domain)
  say "ingest $BACKFILL ($BANDS) shard $IDX to Coralogix ($DOMAIN)"
  ./metricgen bench --targets "coralogix=https://ingress.$DOMAIN:443/v1/metrics" \
    --bands "$BANDS" --shard "$IDX" --shards "$CX_SHARDS" --backfill "$BACKFILL" --interval 1m --seed 1 \
    --out /opt/bench/results/cxload || die "coralogix ingest failed shard $IDX" ingest-coralogix
  mark "02-cx-shard-$IDX" done
  if [ "$IDX" = "0" ]; then
    for s in $(seq 1 $(( CX_SHARDS - 1 ))); do waitfor "02-cx-shard-$s"; done
    say "coralogix ingest complete; 120s settle"
    sleep 120
    mark 03-coralogix-data "ingested"
  fi
  selfterm "cx-sender-$IDX"
  ;;

# ===========================================================================
harness)
  case "$IDX" in
    0) ENG=coralogix ; BACKEND=coralogix
       export CX_PROMQL="https://api.$(cxsecret domain)/metrics/api/v1"; export CX_API_KEY=$(cxsecret api_key) ;;
    1) ENG=mimir ; BACKEND=mimir
       export MIMIR_PROMQL="http://$MIMIR0:8080/prometheus/api/v1"
       export MIMIR_NODES=$(for i in $MIMIR_IPS; do printf 'http://%s:8080/prometheus/api/v1,' "$i"; done | sed 's/,$//')
       # Mimir is loaded from Prometheus-derived blocks, so its counter is named
       # the Prometheus way (http_requests_<band>_total), not the native-OTLP way
       export MIMIR_COUNTER_NAME="http_requests_{band}_total" ;;
    2) ENG=prometheus ; BACKEND=prometheus ; export PROM_PROMQL="http://$PROM_IP:9090/api/v1" ;;
    3) ENG=clickhouse ; BACKEND=clickhouse ; export CH_HTTP="http://$CH_IP:8123" ;;
    # ClickHouse TimeSeries runs the same PromQL ladder as Prometheus and Mimir,
    # through the harness's Prometheus-API path. tsinsert writes the generator's
    # own counter name (http_requests_total_<band>), with no OTLP rename.
    4) ENG=clickhouse-timeseries ; BACKEND=prometheus
       export PROM_PROMQL="http://$TS_IP:8123/prometheus/api/v1"
       export PROM_COUNTER_NAME="http_requests_total_{band}" ;;
    *) die "unknown harness index $IDX" harness ;;
  esac
  waitfor "03-$ENG-data"
  # evaluate every query at the fixed instant the coordinator pinned inside the data
  export QUERY_EVAL_TIME=$(aws s3 cp "s3://$B/$P/data-eval-time" - --region $REGION 2>/dev/null | tr -d '[:space:]')
  say "query eval time pinned to ${QUERY_EVAL_TIME:-now}"
  OUT=/opt/bench/results/sweep-$ENG; mkdir -p "$OUT"; cd /opt/bench/harness
  RUNGS=R01_pinned,R02_label_values,R03_filter_eq,R04_filter_regex,R05_group_by_gauge,R06_rate_group_by,R07_topk,R08_histogram_quantile,R09_join_group_left,R10_burn_rate

  say "verify $ENG"
  python3 /opt/bench/harness/run.py --backends "$BACKEND" --bands "$BANDS" \
    --phase verify --rungs "$RUNGS" --out /tmp/verify-$ENG > /tmp/verify-$ENG.txt 2>&1
  OKC=$(grep -c '^  ok' /tmp/verify-$ENG.txt || true)
  cp /tmp/verify-$ENG.txt "$OUT/verify.txt"
  [ "$OKC" -ge 1 ] || die "verify produced nothing for $ENG" "verify-$ENG"
  mark "03-verify-$ENG" "$OKC ok"

  push(){ tar czf /tmp/sweep-$ENG.tgz -C /opt/bench/results "sweep-$ENG" 2>/dev/null
          aws s3 cp /tmp/sweep-$ENG.tgz "s3://$B/$P/sweep-$ENG.tgz" --region $REGION >/dev/null 2>&1 \
            && say "uploaded $(du -h /tmp/sweep-$ENG.tgz | cut -f1)" || say "UPLOAD FAILED"; }

  for C in $LEVELS; do
    if [ "$C" = "1" ]; then
      say "concurrency 1 (serial)"
      python3 /opt/bench/harness/run.py --backends "$BACKEND" --bands "$BANDS" \
        --phase serial --rungs "$RUNGS" --repeats 40 --cell-budget 45 --min-repeats 5 \
        --out "$OUT" >> "$OUT/c001.txt" 2>&1
    else
      say "concurrency $C"
      python3 /opt/bench/harness/run.py --backends "$BACKEND" --bands "$BANDS" \
        --phase concurrent --rungs "$RUNGS" --concurrency "$C" --conc-seconds 15 \
        --out "$OUT" >> "$OUT/c$(printf %03d $C).txt" 2>&1
    fi
    push; mark "04-c$(printf %03d $C)-$ENG" done
  done
  push; mark "05-sweep-$ENG" "all levels complete"
  say "sweep complete for $ENG"
  ;;

*) die "unknown role $ROLE" role ;;
esac
say "role finished"
