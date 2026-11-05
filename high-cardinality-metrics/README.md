# Coralogix Metrics vs Popular Open Source Engines

This benchmark measures PromQL query latency and error rate under high cardinality and high concurrency. Five metrics backends at matched monthly spend (Coralogix, Prometheus, ClickHouse MergeTree, ClickHouse TimeSeries and Grafana Mimir) are loaded with the same one-week corpus of 1.8 million active series. Ten queries, from a single pinned series to a multi-window burn-rate alert, run against each backend at four cardinality bands (100k, 200k, 500k and 1M series) and at 10 to 100 queries at once. Every (platform, query, band, concurrency) cell records p50, p95, p99, sample count and error rate.

Results: https://benchmarks.coralogix.com/metrics-concurrency

## What you need

- An AWS account in one region with a VPC, a subnet with outbound internet access and quota for these Graviton (m7g) instances, all created by `infra/main.tf`:
  - two m7g.16xlarge loaders (Prometheus and ClickHouse MergeTree), temporary
  - three m7g.8xlarge servers (Prometheus, ClickHouse MergeTree, ClickHouse TimeSeries)
  - thirteen m7g.xlarge: nine Mimir nodes and four Coralogix senders (the senders are temporary)
  - five m7g.4xlarge query clients, one per platform
  - one t4g.small coordinator
- A Coralogix account (standard, no special flags) with a Send-Your-Data API key and an API key with metrics query access.
- Terraform 1.10 or later, AWS CLI v2, Go 1.27.3 (to build the generator locally), Python 3.9 or later with `numpy` (`pip install numpy`), GNU make.
- Installed on the boxes by their bootstrap scripts: ClickHouse 26.10.2.103 (MergeTree and TimeSeries), Prometheus 3.1.0, Grafana Mimir and mimirtool 2.14.3, OpenTelemetry Collector Contrib 0.164.0, Go 1.27.3.
- Cost of one run (`SCALE=real`, `REUSE=0`): about $681, infrastructure and Coralogix ingest. It uses the m7g on-demand prices behind the page's monthly costs (m7g.8xlarge $1.4552 an hour, each m7g size priced in proportion to its vCPUs), t4g.small at $0.0184 an hour and gp3 at $0.08 per GB-month.

  | Item | Count and price | Hours | Cost |
  |---|---|---|---|
  | Serving nodes | 3 x m7g.8xlarge at $1.4552 = $4.3656/h | 30 | $131 |
  | Mimir nodes | 9 x m7g.xlarge at $0.1819 = $1.6371/h | 30 | $49 |
  | Query clients | 5 x m7g.4xlarge at $0.7276 = $3.6380/h | 30 | $109 |
  | Coordinator | 1 x t4g.small at $0.0184/h | 30 | $1 |
  | Loaders | 2 x m7g.16xlarge at $2.9104 = $5.8208/h | 17 | $99 |
  | Coralogix senders | 4 x m7g.xlarge at $0.1819 = $0.7276/h | 17 | $12 |
  | gp3 on the boxes that stay | 2,120 GB (300 + 2 x 600 + 9 x 50 + 5 x 30 + 20) at $0.08/730 per GB-hour | 30 | $7 |
  | gp3 on the boxes that terminate early | 1,020 GB (600 + 300 + 4 x 30) at $0.08/730 per GB-hour | 17 | $2 |
  | Coralogix ingest | one week of the corpus: $1,178 a month x 168 / 730 hours | | $271 |
  | Total | | | about $681 |

  The hours: the loaders and senders run for the ingest, about 17 hours (the ClickHouse MergeTree load is the longest, `infra/stage.sh`), and terminate themselves; the table assumes all of them run that long. Every other box runs until the coordinator tears the fleet down after the last sweep, so it runs for the ingest plus the slowest platform's sweep. A sweep is 400 concurrent cells (4 bands x 10 queries x 10 levels) and 40 serial cells. A concurrent cell takes 15 s if every request is fast and up to 135 s if requests run to the 120 s client timeout; a serial cell takes at most 45 s plus one request. That puts one platform's sweep between about 2 hours (400 x 15 s plus the serial cells) and 17 hours (400 x 135 s + 40 x 165 s); a platform that times out in most of its cells takes about 13 hours, so the estimate uses 17 + 13 = 30 hours. At the 17-hour upper bound the boxes that stay run 34 hours and the run costs about $720.

  Not included: S3 for the corpus and Mimir's blocks (a few hundred GB, under $1 a day), internet egress for the Coralogix ingest, and a NAT gateway if the subnet reaches the internet through one. `SCALE=test` runs on small instances for well under an hour. `REUSE=1` skips the 17-hour ingest and the Coralogix ingest; the loaders and senders still start, and the Prometheus loader backfills Mimir from the stored blocks before they terminate.

## Quick start

```sh
cp .env.example .env   # fill in every value
make setup             # create the bucket and the Coralogix secret, upload the code, terraform init
make run               # provision the fleet; it loads, verifies and sweeps, then tears itself down; results land in results/
make score             # per-cell summaries, results/page-data.json and results/figures.txt
make clean             # terraform destroy; the bucket, corpus and secret are kept for reuse
```

`SCALE` in `.env` picks the size: `test` is a small rehearsal (band b1, 20 minutes of data, 1 and 10 queries at once) on small instances, `real` is the full run. `REUSE=1` mounts the corpus already stored in the bucket: Prometheus and ClickHouse MergeTree serve it directly, Mimir is backfilled from its blocks and Coralogix is sent nothing; ClickHouse TimeSeries has no stored corpus, so a reuse run leaves it out. Values given on the command line override `.env`, for example `make run SCALE=real REUSE=0`.

## Step by step

1. **Configure.** Sign in to AWS (`aws sso login` or keys in the environment), then copy the settings file and fill in every value: region, bucket, run tag, your public IP, an arm64 Amazon Linux 2023 AMI, the VPC and subnet, a free address prefix in that subnet, the Coralogix keys and domain, and the Secrets Manager name that will store the keys.

   ```sh
   cp .env.example .env
   ```

   `make setup` creates the bucket and the secret, uploads `infra/stage.sh`, the generator and the harness to `s3://$BENCH_BUCKET/$RUN_TAG/code/` and runs `terraform init`. Rerun it after changing any code. A rehearsal checks the whole path cheaply before the full run:

   ```sh
   make setup
   make run SCALE=test
   make clean SCALE=test
   ```

2. **Provision five matched backends.** Every self-hosted platform runs on m7g (Graviton) instances in one region, with the query clients in the same private subnet as the self-hosted nodes. `infra/main.tf` creates them:
   - Coralogix (SaaS): a standard account with no special flags, billed on ingest at about $1,178 per month for this volume.
   - Prometheus: 1 node, m7g.8xlarge (32 vCPU / 128 GiB), local TSDB on gp3 (300 GB provisioned).
   - ClickHouse (MergeTree): 1 node, m7g.8xlarge, MergeTree tables on gp3 (600 GB provisioned), loaded from a separate m7g.16xlarge loader.
   - ClickHouse (TimeSeries): 1 node, m7g.8xlarge, the TimeSeries engine on gp3 (600 GB provisioned), loaded on the node itself.
   - Grafana Mimir: 9 nodes, m7g.xlarge each (36 vCPU / 144 GiB in total), replication factor 3, memberlist ring, blocks on S3, 50 GB gp3 per node.

   The matched monthly spend uses m7g on-demand prices: one m7g.8xlarge at $1.4552 an hour for 730 hours is $1,062, plus 1 TB of gp3 at $80, $1,142 a month; nine m7g.xlarge are $1,195, plus 450 GB of gp3 at $40, $1,235 a month.

   ```sh
   make run SCALE=real
   ```

   `make run` applies `infra/main.tf`, waits for the `06-data-safe` marker (up to 48 hours, `WAIT_MAX`) and downloads one result tarball per platform into `results/`.

   Steps 3 to 12 describe what the boxes do during `make run`; their commands are for manual runs only.

   `infra/stage.sh` drives every box by role: loaders, servers, Coralogix senders, one query client per platform and a coordinator. Boxes coordinate through markers in `s3://$BENCH_BUCKET/<run tag>/`; `make run` prints the run tag and writes it to `results/run-tag`. Watch for a marker with `RUN_TAG=$(cat results/run-tag) scripts/wait-marker.sh <marker>` after loading the settings (`set -a; . ./.env; set +a`).

3. **Keep every configuration stock, except what loading a backfilled week needs.** Leave every query timeout, cache, concurrency limit and sharding setting at its default, Coralogix included. Timeouts are:
   - the query client: 120 s per request (`TIMEOUT` in `harness/run.py`), the same for every platform; a request past it is an error
   - Prometheus: its stock `--query.timeout` of 2 minutes
   - Mimir: its default querier timeout
   - ClickHouse MergeTree and TimeSeries: no `max_execution_time`, so the 120 s client timeout is the cap
   - Coralogix: none configured

   Mimir accepts a backfilled week only with these settings changed from stock, all in `infra/user-data-mimir.sh.tftpl`:

   | Setting | Value | Default | Why |
   |---|---|---|---|
   | `limits.max_global_series_per_user` | 2,500,000 | 150,000 | The corpus has 1.8M active series; at the default Mimir rejects about 92% of them. |
   | `limits.ingestion_rate` | 1,000,000 | 10,000 | Samples per second per tenant; the default throttles any write of the corpus through the distributor. |
   | `limits.ingestion_burst_size` | 3,000,000 | 200,000 | The burst allowance that goes with the raised ingestion rate. |
   | `limits.out_of_order_time_window` | 1h | 0 | Accepts samples up to an hour older than the newest sample of a series, so a write of the corpus through the distributor is not rejected for arriving out of order. |
   | `limits.compactor_block_upload_enabled` | true | false | Lets `mimirtool backfill` upload the Prometheus blocks. |
   | `querier.query_store_after` | 0s | 12h | Every series is in uploaded blocks and none is in the ingesters, so the querier must read the store for every time range; at the default the last 12 hours, where the query instant lies, come back empty. |
   | `blocks_storage.bucket_store.ignore_blocks_within` | 0s | 10h | At the default the store-gateway ignores the blocks covering the last 10 hours of the week. |
   | `blocks_storage.bucket_store.sync_interval` | 1m | 15m | The store-gateway picks up uploaded blocks within a minute. |
   | `compactor.cleanup_interval` | 2m | 15m | The compactor rebuilds the bucket index, which the querier needs to see uploaded blocks, every 2 minutes. |

   `limits.max_global_series_per_metric` is set to 0, its default (no per-metric limit). The rest of the file is plumbing: S3 storage, memberlist members, a single tenant.

   ClickHouse TimeSeries needs three changes from stock to create and query the table, all in `infra/user-data-ch-timeseries.sh`:
   - `allow_experimental_time_series_table = 1` and `enable_time_series_table = 1` in `users.d`
   - `custom_settings_prefixes = stats,SQL_` in `config.d`; without it ClickHouse rejects the query client's `stats=all` parameter
   - the `prometheus_api_v1` HTTP handler on `prometheus.metrics`, serving PromQL at `:8123/prometheus/api/v1`

   No `async_insert` or other server setting is changed on either ClickHouse.

4. **Generate one deterministic corpus.** Build the generator and use one fixed seed (`--seed 1`) for every platform. The corpus is 1,800,000 active series at 60-second resolution over one week: 10,080 points per series, about 18.1 billion samples. The four cardinality bands have 100k, 200k, 500k and 1,000,000 active series. Every series carries cluster, namespace, service, pod, region, http_route, http_method, status_code and customer_id.

   ```sh
   cd generator && go build -o metricgen ./cmd/metricgen
   ./metricgen plan
   ```

5. **Load Prometheus and Mimir.** In `infra/stage.sh` (role `pm-loader`) the generator pushes the week over the OTLP receiver of a loader Prometheus on an m7g.16xlarge. The loader takes an admin TSDB snapshot, the snapshot's blocks go to S3, and the serving Prometheus starts on those blocks (role `prom-server`). Mimir is loaded from the same blocks with mimirtool, once per block:

   ```sh
   ./metricgen bench --targets "prometheus=http://127.0.0.1:9090/api/v1/otlp/v1/metrics" \
     --bands b1,b2,b3,b4 --shard 0 --shards 1 --backfill 168h --interval 1m --seed 1
   curl -XPOST 'http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot?skip_head=false'
   mimirtool backfill <snapshot-dir>/<block-ulid> --address=http://<mimir-node>:8080 --id=anonymous
   ```

6. **Load ClickHouse MergeTree.** In `infra/stage.sh` (role `ch-loader`) one generator (`--shards 1`) and one OpenTelemetry Collector on the m7g.16xlarge loader write the week to a local ClickHouse. The generator spends at least 6 s on each one-minute tick (`--backfill-min-tick 6s`). The collector's ClickHouse exporter batches 10,000 to 50,000 rows, flushes every 5 s and runs 8 consumers (`infra/user-data-ch.sh`). The loader then runs `OPTIMIZE TABLE ... FINAL` on every table, `FREEZE`s it and uploads one tarball per part to S3; the serving node creates the same tables and `ATTACH`es every part (role `ch-server`).

   ```sh
   ./metricgen bench --targets "clickhouse=http://127.0.0.1:4318/v1/metrics" --backfill-min-tick 6s \
     --bands b1,b2,b3,b4 --shard 0 --shards 1 --backfill 168h --interval 1m --seed 1
   ```

7. **Load ClickHouse TimeSeries.** In `infra/stage.sh` (role `ts-server`) the serving node creates the table and loads the week into it with `metricgen tsinsert` (`generator/cmd/metricgen/tsinsert.go`). Each insert is `INSERT INTO prometheus.metrics (metric_name, tags, samples) FORMAT JSONEachRow` through `clickhouse-client`, one row per series with its whole week of samples, 4,000 series per insert, 24 inserts at once:

   ```sh
   clickhouse-client --query "CREATE DATABASE IF NOT EXISTS prometheus"
   clickhouse-client --query "CREATE TABLE prometheus.metrics ENGINE = TimeSeries"
   ./metricgen tsinsert --bands b1,b2,b3,b4 --backfill 168h --interval 1m --seed 1 \
     --table prometheus.metrics --chunk 4000 --parallel 24
   ```

8. **Load Coralogix.** Send the week to the account's metrics endpoint with historical timestamps. `infra/stage.sh` (role `cx-sender`) runs four writers, one per shard, 0 to N-1.

   ```sh
   export CORALOGIX_KEY=<send-key>
   ./metricgen bench --targets "coralogix=https://ingress.<domain>:443/v1/metrics" \
     --bands b1,b2,b3,b4 --shard <i> --shards <N> --backfill 168h --interval 1m --seed 1
   ```

9. **Confirm idle.** The loaders and senders terminate themselves once their data is in S3 or on the account, leaving each platform at its footprint from step 2. Each query node runs nothing but its engine before the first measured query.

10. **Pin the query instant and prepare the query ladder.** The coordinator writes one instant (`QUERY_EVAL_TIME`, unix seconds) to the `data-eval-time` marker: its clock minus 300 s, read once the servers are ready. Every loader starts after that point and writes the week that ends at its own start time, so the instant lies inside every engine's loaded week. Every query on every platform is evaluated at that instant. On a `REUSE=1` run the instant comes from the corpus manifest. The ten PromQL queries are in `harness/queries.py`, lightest to heaviest. Coralogix, Prometheus, Mimir and ClickHouse TimeSeries run the same PromQL, with each backend's counter name (`COUNTER_NAME` in `harness/queries.py`), through the harness's Prometheus-API path; ClickHouse MergeTree runs the SQL translations in `harness/queries_ch.py`. Each client runs a verify phase first.
   1. A fully pinned single-series read
   2. Label-values enumeration
   3. An equality label filter
   4. A regex label filter
   5. `sum by (service)` on a gauge
   6. `sum by (service) (rate(...[5m]))`
   7. `topk(10, ...)`
   8. `histogram_quantile(0.99, ...)` p99
   9. A `group_left` join onto an info metric
   10. A multi-window multi-burn-rate SLO alert

   ```sh
   export QUERY_EVAL_TIME=<unix-seconds-inside-the-week>
   export PROM_PROMQL=http://<prometheus-ip>:9090/api/v1
   python3 harness/run.py --backends prometheus --bands b1,b2,b3,b4 --phase verify --out <dir>
   ```

11. **Sweep cardinality and concurrency.** Each platform has its own m7g.4xlarge query client (`infra/stage.sh`, role `harness`, index 0 Coralogix, 1 Mimir, 2 Prometheus, 3 ClickHouse MergeTree, 4 ClickHouse TimeSeries), with the same rungs, bands, levels and windows. It runs the full ladder at each band, first serially (concurrency 1: up to 40 repeats per cell, a 45 s budget, at least 5 repeats), then at 10, 20, 30, 40, 50, 60, 70, 80, 90 and 100 queries at once. The published cells are 10 to 100 at once: 5 platforms x 10 queries x 4 bands x 10 levels, 2,000 cells. Each cell is closed-loop: exactly C workers each send the query, wait for the response and send again with no think time. Workers stop starting new requests after 15 s (`--conc-seconds 15`); requests in flight finish, so with the 120 s client timeout a cell can run to about 135 s. Every completed request is recorded. A cell where no request completes is recorded as all-fail (stopped answering), not as a latency.

    ```sh
    # one platform, one level; repeat for C = 10 to 100 in steps of 10
    python3 harness/run.py --backends <coralogix|prometheus|mimir|clickhouse> --bands b1,b2,b3,b4 \
      --phase concurrent --concurrency <C> --conc-seconds 15 --out results/sweep-<engine>
    # concurrency 1
    python3 harness/run.py --backends <backend> --bands b1,b2,b3,b4 \
      --phase serial --repeats 40 --cell-budget 45 --min-repeats 5 --out results/sweep-<engine>
    ```

    Endpoints for manual runs:

    ```sh
    export CX_PROMQL=https://api.<domain>/metrics/api/v1 CX_API_KEY=<api-key>
    export MIMIR_PROMQL=http://<mimir-node>:8080/prometheus/api/v1
    export MIMIR_NODES=<comma-separated Mimir node URLs>
    export MIMIR_COUNTER_NAME='http_requests_{band}_total'
    export CH_HTTP=http://<clickhouse-mergetree-ip>:8123
    export PROM_PROMQL=http://<prometheus-ip>:9090/api/v1
    # ClickHouse TimeSeries: --backends prometheus, with
    export PROM_PROMQL=http://<clickhouse-timeseries-ip>:8123/prometheus/api/v1
    export PROM_COUNTER_NAME='http_requests_total_{band}'
    ```

    `tsinsert` writes the generator's counter name, `http_requests_total_<band>`; Prometheus's OTLP receiver renames it to `http_requests_<band>_total`, so the TimeSeries client sets `PROM_COUNTER_NAME`. Put each platform's output in `results/sweep-<engine>/` (`coralogix`, `prometheus`, `clickhouse`, `clickhouse-timeseries`, `mimir`); `make run` downloads them there.

12. **Compute engine time.** Coralogix answers over the public internet; the self-hosted platforms share the query client's private network. The network floor is the fastest any Coralogix query of any shape completed in the run: the lowest latency over every successful Coralogix request in `results/sweep-coralogix/` (every query, band and concurrency, the serial phase included). `analysis/build_page_data.py` computes it from the run's own measurements, stores it in `results/page-data.json` as `coralogixFloorMs` with the request it came from (`coralogixFloorFrom`), and prints it. Engine time subtracts the floor from every Coralogix time and leaves the self-hosted times unchanged. Nothing needs setting; `make score` does it.

13. **Compare with the page.** Compare each figure in the next section with the results page. `make score` writes `results/summary-<engine>.txt`, `results/page-data.json` (in the format the page reads) and `results/figures.txt`.

    ```sh
    make score
    ```

14. **Clean up.** The coordinator terminates every instance once the result tarballs are in S3. Remove the rest of the Terraform resources (IAM role and profile, security group); the bucket, the corpus under `s3://$BENCH_BUCKET/corpus/` and the secret are kept for a `REUSE=1` run. Delete them by hand when finished.

    ```sh
    make clean SCALE=real
    set -a; . ./.env; set +a
    aws s3 rm "s3://$BENCH_BUCKET" --recursive && aws s3api delete-bucket --bucket "$BENCH_BUCKET"
    aws secretsmanager delete-secret --secret-id "$CX_SECRET_ID" --force-delete-without-recovery
    ```

## How each figure is computed

- **Matched monthly spend:** m7g on-demand prices for 730 hours plus gp3 storage. Prometheus, ClickHouse MergeTree and ClickHouse TimeSeries: m7g.8xlarge at $1.4552 an hour ($1,062) plus 1 TB of gp3 ($80), $1,142. Mimir: 9 x m7g.xlarge ($1,195) plus 450 GB of gp3 ($40), $1,235. Coralogix: its ingest bill for this volume, about $1,178. Terraform provisions 300 GB for Prometheus, 600 GB for each ClickHouse and 50 GB per Mimir node.
- **Per-cell latency:** p50, p95 and p99 of successful requests, with sample count and error rate, for every (platform, query, band, concurrency) cell from 10 to 100 at once. `analysis/build_page_data.py`, written to `results/page-data.json`; `harness/summarise.py` prints per-cell tables per platform to `results/summary-<engine>.txt`.
- **Coralogix network floor:** the fastest any Coralogix query of any shape completed in the run, the minimum latency over every successful request in `results/sweep-coralogix/`. `analysis/build_page_data.py` (`coralogixFloorMs` and `coralogixFloorFrom` in `results/page-data.json`); `analysis/figures.py` prints it first.
- **Engine time:** Coralogix p50 minus the network floor (never below 0.01 ms); self-hosted p50 unchanged. `analysis/build_page_data.py` and `analysis/figures.py`, both reading `coralogixFloorMs`.
- **Stopped answering:** a cell where every request failed or timed out at 120 s (`allFail` in `results/page-data.json`).
- **Cell winner:** the platform with the lowest engine-time p50 among those with no errors in that cell; a platform with errors cannot win. `analysis/build_page_data.py` (`win`, `wins`).
- **Summary table:** every query at 1M series and one concurrency level (50 by default, `--summary-concurrency`), engine time, winner marked. `analysis/figures.py`, section 1.
- **Error grid:** per platform, band and 10, 30, 50 and 100 at once, the error rate averaged over the ten queries with each query weighted equally; a stopped cell counts as 100%. `analysis/figures.py`, section 2.
- **Latency grid:** the same cells, the median of the ten query medians in engine time. `analysis/figures.py`, section 3.
- **Impact of concurrency:** rate by service (query 6) at 1M series, p50 from 10 to 100 at once. `analysis/figures.py`, section 4.
- **Impact of cardinality:** rate by service at 30 at once, p50 at each band. `analysis/figures.py`, section 5.
- **All results:** p50 for every query, platform and band at 10, 30, 50 and 100 at once, winners marked. `analysis/figures.py`, section 6.

## Layout

```
Makefile, .env.example   entry points and settings
infra/                   main.tf (the fleet), stage.sh (every box's role in the run), and one bootstrap per box type:
                         user-data-prom.sh, user-data-ch.sh (MergeTree), user-data-ch-timeseries.sh,
                         user-data-mimir.sh.tftpl, user-data-worker.sh (senders, query clients, coordinator)
generator/               metricgen: the deterministic corpus (plan, bench, tsinsert)
harness/                 run.py (query client), queries.py (PromQL ladder), queries_ch.py (SQL ladder), summarise.py
analysis/                build_page_data.py (cells, engine time, winners), figures.py (every figure on the page)
scripts/                 setup.sh, run.sh, score.sh, clean.sh, wait-marker.sh
results/                 created by make run and make score; not committed
```
