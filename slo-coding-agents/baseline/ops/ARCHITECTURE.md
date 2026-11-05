# larder-ops architecture

_Last reviewed: 2023-08 (p.ndlovu), after the fulfilment split. Some of this
has drifted; when in doubt, read the code._

## History

Until mid-2023 everything here was one Express app, `larder-fulfilment`
(inventory, slots, order confirmation, PSP, partner webhooks). ARCH-12 split it
into the services below. Shared code moved to `lib/`. The split was done
"lift and shift": most modules were copied with their comments intact, so
comments that mention fulfilment, in-process calls or `lib/orders.js` are
describing the old monolith.

## Services

| Service | Port (dev) | Owns | Talks to |
|---|---|---|---|
| orders | 8111 | `orders.json` | inventory, tax, slots, payments, ledger, bus |
| inventory | 8112 | `inventory.json`, `audit.log` | - |
| slots | 8113 | `slots.json`, `holds.json`, `zones.json` | - |
| payments | 8114 | `payments.json` | fraud, PSP |
| fraud | 8115 | nothing (stateless rules) | - |
| ledger | 8116 | `ledger.json` | - |
| tax | 8117 | nothing | - |
| webhooks | 8118 | `webhook_queue.json` | partner |
| warehouse | 8119 | `warehouse.json` | orders, inventory, bus |
| notifications | 8120 | `notifications.json` | email provider |
| bus | 8121 | `bus.json` | subscribers |

Each service is started with
`node --require ./lib/telemetry/otel.js services/<name>/server.js` from the
repo root and gets its port from `PORT`. Peers are found through
`<NAME>_URL` env vars; `config/default.json` has dev defaults that point at
the mesh ports (8200 + i).

## Order flow

```
checkout (commerce) -> orders POST /internal/orders
    orders -> inventory  /internal/inventory/prices        (server prices)
    orders -> tax        /internal/tax/totals              (canonical totals)
    orders -> inventory  /internal/inventory/reservations  (per line)
    orders -> slots      /internal/slot-holds/:id/confirm
    orders -> payments   /internal/payments/charge   -> fraud, PSP
    orders -> ledger     /internal/ledger/entries
    orders -> bus        /publish order.confirmed
bus -> webhooks, notifications, warehouse, ledger, loyalty   (in parallel)
```

Orders are idempotent on `order_ref`: a replayed POST returns the original
order.

## The bus

Homegrown. `POST /publish` stores the message in `bus.json` and fans it out
to the subscribers listed in `config/bus-subscriptions.json`. Delivery is
at-least-once with retries, so subscribers should be idempotent. Each
subscriber has its own retry loop with exponential backoff, so a slow
subscriber only affects itself.

## Dispatch

The warehouse floor (loadgen in the bench) calls
`orders POST /internal/orders/:ref/dispatch`, which takes the stock out of
inventory and publishes `order.dispatched`.

## Shared library (`lib/`)

* `lib/service.js`: Express scaffolding (`/healthz`, error handling, boot,
  shutdown). Every service uses it.
* `lib/config.js`: config loading. Env vars win over the JSON files.
* `lib/telemetry/`: OpenTelemetry. **Owned by the platform team.**
* `lib/logger.js`: winston 2 + OTel log bridge.
* `lib/http/`: HTTP clients. We are standardising on axios.
* `lib/bus-client.js`: publish + subscriber router.
* `lib/pool.js`: connection pool used by slots and orders.
* `lib/money.js`: the money helpers. Use these for totals.
* `vendor/jdb`: the JSON document store (vendored, upstream deleted).

## Data

Each service writes JSON files under `LARDER_DATA_DIR`
(`data/<service>` in the bench). Delete the directory to reseed;
inventory and slots reseed from `seed/` on an empty dir.

## Telemetry

OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`. Metric names are fixed by the
platform contract (`ops_*`). Don't add `unit` to instruments.
