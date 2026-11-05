# Changelog

## 4.2.0 (2024-06-03)
- Platform: telemetry moved into larder_common, metrics renamed `commerce_*`.
- search: live prices for the top results (MERCH-388).
- Account page shows loyalty points (backend only).

## 4.1.0 (2023-09-12)
- recommendations: "customers also bought" on product pages (PM-990).
- catalog: `POST /internal/products/bulk`.

## 4.0.0 (2023-04-03) - Project Pantry
- storefront split into ten services (see ARCHITECTURE.md).
- Orders, zones and slots moved to ops.
- `larder` package -> `libs/larder_common` + `services/*`. settings_local.py
  moved with it.

## 3.14.2 (2024-03-11)
- Platform: OpenTelemetry replaces statsd (telemetry.py owned by platform team).
- Fix: account page counted failed orders.

## 3.14.0 (2023-11-02)
- Order status refreshed from fulfilment when viewing an order.
- Bump Flask to 2.2.5.

## 3.12.0 (2023-02-14)
- Moved packaging to pyproject.toml.
- Delivery slots now come from fulfilment (/internal/slots), local slot table removed.

## 3.9.0 (2022-06-21)
- New checkout: storefront generates `order_ref`, fulfilment owns payment.
- `*_pence` fields added to cart and order responses (TD-1, phase 1).

## 3.4.0 (2021-04-08)
- MySQL -> SQLite on the app nodes (one db per node, carts are sticky).
- Search cache moved in-process (Redis cluster decommissioned).

## 3.0.0 (2020-02-03)
- v2 pricing engine.

## 2.3.0 (2018-05-14)
- Baskets replaced by carts. /v1/basket deprecated.
