# Workspace guide

```
.
├── AGENT_INSTRUCTIONS.md   your brief for this session
├── BACKLOG.md              feature requests and operational tickets
├── REPORT_TEMPLATE.md      template for REPORT.md (write it before the deadline)
├── WORKSPACE.md            this file
├── larderctl               service control (status / restart / logs / time)
├── commerce/               Python monorepo (10 services) - git repo
├── ops/                    Node.js monorepo (11 services) - git repo
└── .larder/                runtime files: service logs, environment for cx
```

## Services

Every service listens on its app port and is reached by other services through the mesh port. Peers
are discovered through `<NAME>_URL` environment variables that point at mesh ports
(e.g. `CATALOG_URL=http://127.0.0.1:8202`). Customer traffic enters at the edge on port 8000.

| # | Service | Repo | App port | Mesh port |
|---|---|---|---|---|
| 1 | edge-api | commerce | 8101 | 8000 (customer edge) |
| 2 | catalog | commerce | 8102 | 8202 |
| 3 | search | commerce | 8103 | 8203 |
| 4 | pricing | commerce | 8104 | 8204 |
| 5 | promotions | commerce | 8105 | 8205 |
| 6 | cart | commerce | 8106 | 8206 |
| 7 | checkout | commerce | 8107 | 8207 |
| 8 | accounts | commerce | 8108 | 8208 |
| 9 | loyalty | commerce | 8109 | 8209 |
| 10 | recommendations | commerce | 8110 | 8210 |
| 11 | orders | ops | 8111 | 8211 |
| 12 | inventory | ops | 8112 | 8212 |
| 13 | slots | ops | 8113 | 8213 |
| 14 | payments | ops | 8114 | 8214 |
| 15 | fraud | ops | 8115 | 8215 |
| 16 | ledger | ops | 8116 | 8216 |
| 17 | tax | ops | 8117 | 8217 |
| 18 | webhooks | ops | 8118 | 8218 |
| 19 | warehouse | ops | 8119 | 8219 |
| 20 | notifications | ops | 8120 | 8220 |
| 21 | bus | ops | 8121 | 8221 |

Every service answers `GET /healthz`. Runtime data lives in `commerce/var/<service>/` and
`ops/data/<service>/` and survives restarts. The supervisor that runs the services sets their
environment (ports, peer URLs, data dirs, OpenTelemetry exporter settings).

```
./larderctl status                  # all services: up, pid, restarts, ports; session clock
./larderctl restart checkout        # one service
./larderctl restart ops             # every service in a repo (or: commerce, all)
./larderctl logs payments -n 200    # recent stdout/stderr (also in .larder/logs/)
./larderctl stop|start <service>
./larderctl time                    # minutes remaining in the session
```

If a service crashes, the supervisor restarts it automatically with backoff.

Tests: `cd commerce && make test` (uses `uv run pytest`); `cd ops && npm test`.

Exercise the live system through the edge or the mesh, e.g.
`curl -s localhost:8000/api/products?per_page=3` or `curl -s localhost:8211/healthz`.

## Telemetry in Coralogix

Everything Larder emits goes to Coralogix with application name `larder` and subsystem = service
name. Telemetry from this session carries the label/attribute `bench_run_id` = `$LARDER_RUN_ID` (see
`.larder/agent.env`). **Filter on it**, because the account may also hold data from earlier sessions.

| Source | Subsystem / service | What |
|---|---|---|
| edge + mesh proxies | `ingress` | per-request access logs; metrics `larder_ingress_requests_total` and `larder_ingress_request_duration_seconds_*` with labels `service` (the service being called), `route`, `method`, `code`, `code_class`, `reason` (error reason from 4xx/5xx JSON bodies) |
| edge proxy (per entity) | `ingress` | `larder_ingress_entity_requests_total` with labels `entity_type` (`user`, `sku` for product pages, `postcode` for slot listings), `entity`, `code_class`: customer-facing requests broken down per customer, product and postcode |
| supervisor | `supervisor` | start/exit/restart events; `larder_service_up`, `larder_service_restarts_total`, `larder_process_cpu_percent`, `larder_process_rss_bytes` (label `service`) |
| each service | its own name | traces (joined across services), logs, `commerce_*` / `ops_*` metrics (label `service_name`) |

Dashboards: **Larder · Service overview** and **Larder · Runtime and dependencies**. Alerts are named
`Larder · ...`. Traces give a service map across all 21 services.

Useful commands (`cx --help` for more):

```
cx dashboards catalog                     # find dashboards (ids)
cx alerts list                            # alert definitions and current status
cx alerts events --start now-30m          # recent alert firings
cx metrics search --name ops_             # metric names
cx metrics query 'sum by (service, route, code) (rate(larder_ingress_requests_total{bench_run_id="'$LARDER_RUN_ID'",code_class="5xx"}[5m]))'
cx logs "source logs | filter \$l.subsystemname == 'payments' && \$m.severity == ERROR | limit 20"
cx spans "source spans | filter \$l.serviceName == 'checkout' && \$m.duration > 1000000 | limit 20"
```

DataPrime string literals use single quotes. Span `duration` is in microseconds. Query logs and spans in the default
(archive) tier, where every field is structured; in the Frequent Search tier some records appear only as raw text.
