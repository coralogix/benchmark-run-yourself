# Larder Commerce

Customer-facing commerce services for the Larder online shop: catalogue,
search, pricing, promotions, basket, checkout, accounts, loyalty and
recommendations, plus the edge API the apps talk to.

> Maintainers: Web Team (#web-storefront). On-call: see the Ops wiki.

This repo used to be the `storefront` monolith. It was split into services
in 2023 ("Project Pantry"); see `ARCHITECTURE.md`.

## Services

| Service | Port | What |
|---|---|---|
| edge-api | 8000 | public API (`/api/*`, `/v1/*`) |
| catalog | 8102 | products, categories |
| search | 8103 | product search |
| pricing | 8104 | basket pricing |
| promotions | 8105 | vouchers |
| cart | 8106 | baskets |
| checkout | 8107 | checkout |
| accounts | 8108 | customers |
| loyalty | 8109 | points (coming soon) |
| recommendations | 8110 | "customers also bought" |

Orders, stock, delivery slots and payments belong to the Ops team's
`fulfilment` service.

## Running locally

```bash
uv sync
make run-all          # every service, ports as above
make smoke            # happy path against edge-api
```

Or one service: `make run SVC=catalog`.

## Configuration

All configuration lives in `config/defaults.ini`. Any value can be overridden
with an environment variable (environment always wins):

| Variable | Default | Notes |
|---|---|---|
| `PORT` | per service | |
| `LARDER_DATA_DIR` | `./var` | sqlite files |
| `LARDER_PRICING_ENGINE` | `v2` | `v1` is the 2016 engine, kept for /v1/price-check |
| `LARDER_HTTP_TIMEOUT` | `10` | seconds, outbound service calls |
| `LARDER_LOG_LEVEL` | `INFO` | |
| `FULFILMENT_URL` | `http://fulfilment.larder.internal:8081` | ops |

Each service finds the others through `<NAME>_URL` variables
(`CATALOG_URL`, `SEARCH_URL`, ...).

## Pricing

Prices are stored in pounds. All basket maths is done by the pricing
service's v2 engine (`services/pricing/engine_v2.py`). The old engine in
`legacy/` is only used by the `/v1` API.

Delivery is £3.99 under £40, free over. The FREESHIP voucher waives it.

## Search

Search keeps a results cache in Redis (10 minutes) and rebuilds its index
nightly.

## Tests

```bash
make test
```

## Deploying

Merges to `master` are deployed by Jenkins (`commerce-deploy` job), one
container per service. Rollback: re-run the job with the previous tag.

## Monitoring

Grafana dashboards: `commerce/*`. OpenTelemetry is set up by the platform
team (`libs/larder_common/telemetry.py`, don't touch).
