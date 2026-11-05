# larder-ops

Fulfilment back office for Larder: inventory, delivery slots, orders,
payments and partner webhooks.

> NOTE (2023-07): this repo used to be `larder-fulfilment`. Most of this
> README predates the split; see ARCHITECTURE.md for the service list.

## Requirements

* Node 8+ (we run 10 in production)
* npm

## Getting started

```
npm install
npm start
```

The app listens on port 8081. Data is stored in `./data`.

## Configuration

Configuration lives in `config/default.json`. Environment variables override
it:

| Variable | Meaning |
|---|---|
| `PORT` | listen port (default 8081) |
| `LARDER_DATA_DIR` | data directory (default `./data`) |
| `PSP_URL` | payment provider base URL |
| `PSP_TIMEOUT_MS` | PSP request timeout |
| `PARTNER_WEBHOOK_URL` | partner webhook endpoint |
| `PARTNER_WEBHOOK_SECRET` | HMAC secret for partner webhooks |
| `LOG_LEVEL` | winston level |

`config/local.js` is for your local overrides and is not used in production.

## API

* `POST /internal/orders` - confirm an order
* `GET /internal/orders/:ref`
* `POST /internal/orders/:ref/dispatch`
* `GET /internal/slots?zone=`
* `POST /internal/slot-holds`
* `POST /internal/inventory/availability`
* `GET /internal/health`

The v1 API (`/v1/*`) was removed in 2020.

## Tests

```
npm test
```

## Linting

ESLint was removed in 2021 (it was failing on the vendored code and nobody
fixed the config). `npm run lint` is a no-op.

## Owners

#fulfilment (archived), then #ops-platform. Telemetry: #platform.
