# Commerce architecture

_Last updated: TH, 2023-04 (after the Project Pantry split)._

## Overview

```
                    apps / web
                        |
                    edge-api  (BFF, public API)
     ___________________|______________________________________
    |        |          |        |          |         |        |
 catalog  search     cart    checkout   accounts   recs    fulfilment (ops)
             |          |        |          |
          catalog    pricing  orders(ops) orders(ops)
                        |
                    promotions
```

Every arrow is HTTP through the service mesh. Services never talk to each
other's databases: each service owns its own sqlite file under
`var/<service>/`.

## Services

- **edge-api** - the only public service. Routes `/api/*` to the right
  backend and adds stock levels from fulfilment. Also serves the old `/v1`
  API. Stateless.
- **catalog** - products and categories (`catalog.db`). Read-mostly.
- **search** - full-text search over the catalogue. Gets products from
  catalog's API and caches results (bounded LRU, 2,000 entries).
- **pricing** - prices baskets. Engine v2 (pence-accurate). Gets prices from
  catalog's API and vouchers from promotions.
- **promotions** - vouchers (`promotions.db`).
- **cart** - baskets (`cart.db`). Stateless; carts are rows in sqlite.
- **checkout** - takes a cart, prices it, and creates the order in ops'
  orders service (which takes payment). Outbound calls have a 10s timeout and
  are never retried for POSTs.
- **accounts** - customers (`accounts.db`) and the account page (order count
  from ops, paginated).
- **loyalty** - points. Subscribed to `order.confirmed` on the ops bus.
- **recommendations** - "customers also bought", from catalog (bulk lookup)
  and search. Cached for 10 minutes.

## Shared code

`libs/larder_common` - config, logging, the HTTP client, the dbx ORM,
telemetry (platform team). Kept deliberately thin.

## Startup order

None required; every service starts independently.

## Data

| Service | File | Notes |
|---|---|---|
| catalog | `var/catalog/catalog.db` | seeded from `seed/products.json` |
| promotions | `var/promotions/promotions.db` | seeded from `seed/coupons.json` |
| accounts | `var/accounts/accounts.db` | seeded from `seed/users.json` |
| cart | `var/cart/cart.db` | |
| checkout | `var/checkout/checkout.db` | checkout attempts |
| loyalty | `var/loyalty/loyalty.db` | |

`seed/zones.json` is the delivery zone map; ops owns zones now.

## Legacy

`legacy/` is the 2016-2018 storefront code. Only `/v1` uses it.
