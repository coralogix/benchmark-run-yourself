# Larder backlog

Exported from the team board this morning. Priorities are the product manager's (P1 highest).
Sizes are the team's rough estimates (S ≈ under 15 minutes, M ≈ 15-40 minutes, L ≈ 40+ minutes).
Money is always integer pence in new fields. Acceptance criteria are what QA will check.
Internal (`/internal/...`) endpoints are served by the named service on its mesh port.

100 feature tickets, followed by operational and tech-debt tickets.

| Ticket | Title | Priority | Size |
|---|---|---|---|
| FEAT-001 | 3-for-2 multi-buy on promotion products | P1 | L |
| FEAT-002 | Customers can cancel an order | P1 | L |
| FEAT-003 | "Next available delivery slot" shortcut | P2 | S |
| FEAT-004 | Search filters and sorting | P2 | M |
| FEAT-005 | Loyalty points | P2 | M |
| FEAT-006 | Partner webhooks v2 (signed) | P2 | M |
| FEAT-007 | VAT receipts | P3 | L |
| FEAT-008 | Order history page | P2 | M |
| FEAT-009 | Substitutions at checkout | P2 | L |
| FEAT-010 | Better order confirmation email | P3 | M |
| FEAT-011 | "Only N left" badge | P3 | S |
| FEAT-012 | One-use-per-customer coupons | P3 | M |
| FEAT-013 | Sort the product listing | P2 | S |
| FEAT-014 | Dietary filter on the product listing | P2 | S |
| FEAT-015 | Category list endpoint | P3 | S |
| FEAT-016 | VAT rate on product pages | P3 | S |
| FEAT-017 | Remove an item from the cart | P1 | S |
| FEAT-018 | Change item quantity in the cart | P1 | S |
| FEAT-019 | Per-line quantity limit | P2 | S |
| FEAT-020 | Empty the cart | P2 | S |
| FEAT-021 | Item count on the cart | P3 | S |
| FEAT-022 | Total savings on the cart | P3 | S |
| FEAT-023 | Request IDs in error bodies | P2 | S |
| FEAT-024 | JSON 405 responses | P3 | S |
| FEAT-025 | Deep health check | P2 | M |
| FEAT-026 | Delivery details on order view | P2 | M |
| FEAT-027 | Line totals on orders | P3 | S |
| FEAT-028 | Change postcode on an account | P2 | M |
| FEAT-029 | Marketing opt-in | P4 | S |
| FEAT-030 | Search autocomplete | P2 | M |
| FEAT-031 | Paginate search results | P2 | S |
| FEAT-032 | Reject very long search queries | P3 | S |
| FEAT-033 | Normalise search queries | P3 | S |
| FEAT-034 | Filter delivery slots by date | P3 | S |
| FEAT-035 | Release a slot hold | P2 | S |
| FEAT-036 | Slot details on a hold | P4 | S |
| FEAT-037 | Require a payment token | P2 | S |
| FEAT-038 | Idempotent checkout | P1 | L |
| FEAT-039 | Minimum order value | P2 | S |
| FEAT-040 | Reject expired slot holds at checkout | P2 | M |
| FEAT-041 | Estimated delivery in the checkout response | P3 | S |
| FEAT-042 | Order status history | P3 | M |
| FEAT-043 | Reorder | P2 | M |
| FEAT-044 | Public "also bought" endpoint | P3 | S |
| FEAT-045 | No out-of-stock recommendations | P3 | M |
| FEAT-046 | Price-appropriate recommendations | P3 | S |
| FEAT-047 | Formatted price on products | P4 | S |
| FEAT-048 | List active promotions | P2 | M |
| FEAT-049 | Coupon expiry on the cart | P3 | S |
| FEAT-050 | Fixed-amount coupon FIVER | P2 | L |
| FEAT-051 | Redeem loyalty points | P3 | L |
| FEAT-052 | Loyalty tiers | P4 | S |
| FEAT-053 | Cancellation email | P3 | M |
| FEAT-054 | Tracking link in dispatch email | P3 | S |
| FEAT-055 | Email suppression list | P3 | M |
| FEAT-056 | Order cancelled webhook | P3 | M |
| FEAT-057 | Open orders on the account summary | P3 | S |
| FEAT-058 | Webhook stats endpoint | P4 | S |
| FEAT-059 | Pick lists by date | P3 | M |
| FEAT-060 | Don't dispatch cancelled orders | P2 | S |
| FEAT-061 | Restock endpoint | P2 | M |
| FEAT-062 | Low-stock report | P3 | S |
| FEAT-063 | Public stock endpoint | P3 | S |
| FEAT-064 | Payments by order | P3 | S |
| FEAT-065 | Refund API | P2 | M |
| FEAT-066 | Fraud rule listing | P4 | S |
| FEAT-067 | Daily ledger totals | P2 | M |
| FEAT-068 | Ledger refund entries | P3 | M |
| FEAT-069 | Public VAT rates | P4 | S |
| FEAT-070 | Delivery VAT in totals | P4 | S |
| FEAT-071 | Event bus lag per topic | P3 | S |
| FEAT-072 | Event bus dead-letter queue | P2 | L |
| FEAT-073 | Adjust slot capacity | P3 | S |
| FEAT-074 | Hide full delivery slots | P3 | S |
| FEAT-075 | Customer registration | P1 | M |
| FEAT-076 | Abandoned cart expiry | P3 | M |
| FEAT-077 | Merge carts | P3 | M |
| FEAT-078 | "Did you mean" for searches | P3 | M |
| FEAT-079 | CORS for the new web app | P2 | S |
| FEAT-080 | API version endpoint | P4 | S |
| FEAT-081 | Gzip responses | P3 | S |
| FEAT-082 | ETags on product detail | P3 | M |
| FEAT-083 | Server-Timing header | P4 | S |
| FEAT-084 | Cart price breakdown | P3 | M |
| FEAT-085 | Prices in euros | P4 | S |
| FEAT-086 | Eligible coupons for a cart | P3 | M |
| FEAT-087 | Order counts by status | P4 | S |
| FEAT-088 | Delivery notes | P3 | M |
| FEAT-089 | Driver tips | P3 | L |
| FEAT-090 | Personal data export | P2 | M |
| FEAT-091 | Account deletion | P2 | M |
| FEAT-092 | Notification history | P4 | S |
| FEAT-093 | Driver on dispatched orders | P3 | S |
| FEAT-094 | Delivery zones endpoint | P4 | S |
| FEAT-095 | Filter products by tag | P3 | S |
| FEAT-096 | In-stock filter | P3 | M |
| FEAT-097 | Top search queries | P4 | S |
| FEAT-098 | Service info endpoints | P4 | M |
| FEAT-099 | Product reviews (read-only) | P3 | L |
| FEAT-100 | Favourites | P3 | M |
| REL-1..REL-5 | Operational tickets (see below) | P2-P3 | ? |
| TD-1, TD-2 | Tech debt (see below) | P3-P4 | ? |

---

## Features

### FEAT-001 · 3-for-2 multi-buy on promotion products · P1 · L
Marketing's "3 for 2" campaign starts **today**; the banners are already live on the website. Products tagged `3for2` (see the `tags` field on products) must be priced so that for each such product in the basket, every third unit is free.

Acceptance criteria:
- For a basket line of a `3for2` product with quantity `q` and unit price `p` pence, the line is charged `(q - floor(q/3)) * p`. Lines for other products are unaffected.
- The cart (`GET /api/carts/<id>`) shows `multibuy_discount_pence` (total, 0 if none) and, per item, `multibuy_free_units`. `subtotal_pence` is after multi-buy savings; coupon percentages apply to that subtotal; the delivery-fee threshold uses the subtotal after coupon, as today.
- A checkout of a basket with multi-buy savings succeeds and the confirmed order total equals the cart's `total_pence`.

### FEAT-002 · Customers can cancel an order · P1 · L
Customer service spends hours on cancellation requests. Add self-service cancellation.

Acceptance criteria:
- `POST /api/orders/<ref>/cancel` on a confirmed order returns 200 `{"order_ref", "status": "cancelled", "refund_pence"}` and the order shows `status: "cancelled"`.
- The full order total is refunded through the PSP **exactly once**, even if the customer clicks cancel twice (a second call returns 200 with the same result or 409 `{"error": "already_cancelled"}`, and must not refund again).
- Reserved stock is returned (the product is purchasable again) and the delivery-slot capacity is released.
- Dispatched orders cannot be cancelled: 409 `{"error": "already_dispatched"}`. Unknown order: 404.

### FEAT-003 · "Next available delivery slot" shortcut · P2 · S
The app team wants a one-call shortcut for the checkout page.

Acceptance criteria:
- `GET /api/delivery/slots/next?postcode=<postcode>` returns 200 `{"zone", "slot": {"slot_id", "date", "window", "available"}}` for the earliest slot (by date, then window start time) with capacity left in the customer's zone.
- 404 `{"error": "no_slots"}` if the zone has none; 400 for an unknown postcode.
- Fast enough to call on every checkout page view (p95 under 300 ms).

### FEAT-004 · Search filters and sorting · P2 · M
Customers keep asking for "vegan" and "under £4" filters.

Acceptance criteria:
- `GET /api/search` accepts optional `diet` (`vegan`, `vegetarian`, `gluten_free`), `max_price_pence` (integer) and `sort` (`price_asc`, `price_desc`, `name`). Filters combine with AND.
- Every result honours the filters; sorting is applied to the full filtered result set.
- Repeating the same `q` with different filters returns correctly filtered results each time.
- Unknown `diet` or `sort` values return 400. Requests without the new parameters behave as today.

### FEAT-005 · Loyalty points · P2 · M
Launching "Larder Points": 1 point per whole pound spent on goods.

Acceptance criteria:
- Each confirmed order earns `floor((subtotal_pence - discount_pence) / 100)` points (goods after discounts, excluding delivery). `GET /api/orders/<ref>` shows `loyalty_points_earned`.
- `GET /api/account/<user_id>` shows `loyalty_points`, the user's total over confirmed orders.
- If order cancellation exists, cancelled orders do not count.
- Loyalty must never stop a customer from completing a checkout.

### FEAT-006 · Partner webhooks v2 (signed) · P2 · M
Our warehouse partner is turning off unsigned webhooks at the end of the week.

Acceptance criteria:
- Every webhook body includes `"schema_version": 2`.
- Every webhook request carries `X-Larder-Signature: sha256=<hex>`: the HMAC-SHA256 of the exact raw request body, keyed with the partner shared secret we already hold in fulfilment's configuration.
- `order.dispatched` events include `data.items`: `[{"sku", "qty"}]`.
- The partner continues to receive `order.confirmed` and `order.dispatched` for every order.

### FEAT-007 · VAT receipts · P3 · L
Business customers need VAT receipts.

Acceptance criteria:
- `GET /api/orders/<ref>/receipt` returns `{order_ref, lines: [{sku, name, qty, unit_price_pence, line_total_pence, vat_band, vat_rate, vat_pence}], subtotal_pence, discount_pence, delivery_fee_pence, total_pence, vat_total_pence, vat_breakdown: {zero, reduced, standard}}`.
- Prices are VAT-inclusive. Rates: `zero` 0%, `reduced` 5%, `standard` 20%. The order discount is spread across lines in proportion to line totals (any leftover pence go to the largest line). Line VAT = round-half-up(discounted line total × rate / (100 + rate)). Delivery is standard-rated.
- All pence fields are integers and add up exactly: discounted line totals + delivery = `total_pence`.
- 404 for an unknown order.

### FEAT-008 · Order history page · P2 · M
The new account page needs a paginated order history.

Acceptance criteria:
- `GET /api/account/<user_id>/orders?page=1&per_page=10` returns 200 `{"orders": [{"order_ref", "status", "total_pence", "created_at"}], "page", "per_page", "total"}`, newest first, containing only that user's orders. `per_page` defaults to 10, maximum 50.
- 404 for an unknown user.
- p95 under 300 ms, and it must stay that fast as the number of orders in the system grows.

### FEAT-009 · Substitutions at checkout · P2 · L
Customers would rather get a similar product than lose the whole order when something runs out.

Acceptance criteria:
- `POST /api/checkout` accepts an optional `"allow_substitutions": true`. Without it, behaviour is unchanged.
- With it, an item that is out of stock is replaced by an in-stock product of the same category that carries every dietary tag the original had, priced at no more than 110% of the original. If several qualify, pick the cheapest. The customer is charged the substitute's price.
- The 201 response includes `"substitutions": [{"sku", "substitute_sku", "qty"}]` (empty if none were needed). The order's items show the substitute.
- If any out-of-stock item has no acceptable substitute, the checkout fails with 409 `out_of_stock` as today.

### FEAT-010 · Better order confirmation email · P3 · M
Customers complain the confirmation email says nothing useful.

Acceptance criteria:
- On order confirmation, the email provider receives a message with template `order_confirmation_v2` whose `data` includes `order_ref`, `items` (`[{"sku", "name", "qty"}]`), `total_pence` and `delivery` (`{"date", "window"}`), sent to the customer's email address.
- Exactly one confirmation email per order.

### FEAT-011 · "Only N left" badge · P3 · S
Merchandising wants urgency messaging on product pages.

Acceptance criteria:
- `GET /api/products/<sku>` includes `low_stock` (true when available stock is 10 or fewer) and `only_n_left` (the available quantity when `low_stock` is true, otherwise null).
- Product listing pages are unchanged and must not make extra per-product calls.
- Product detail p95 must not get noticeably slower.

### FEAT-012 · One-use-per-customer coupons · P3 · M
Finance found customers reusing WELCOME10 dozens of times.

Acceptance criteria:
- `WELCOME10` and `STUDENT20` can be used on at most one confirmed order per customer.
- Applying one of them to a cart for a customer who already has a confirmed order with it returns 400 `{"error": "coupon_already_used"}`.
- If the same customer checks out two carts with the code at the same time, at most one order gets the discount.
- If order cancellation exists, cancelling the order frees the coupon again.

### FEAT-013 · Sort the product listing · P2 · S
Shoppers want to sort category pages by price.

Acceptance criteria:
- `GET /api/products` accepts `sort` = `price_asc`, `price_desc` or `name`; ordering applies across all pages, not just within a page.
- Unknown `sort` values return 400 `{"error": "invalid_sort"}`. Without `sort`, ordering is unchanged.

### FEAT-014 · Dietary filter on the product listing · P2 · S
The vegan filter that exists on search should also exist on category pages.

Acceptance criteria:
- `GET /api/products?diet=vegan|vegetarian|gluten_free` returns only products carrying that tag; `total` reflects the filtered count.
- Unknown diet values return 400 `{"error": "invalid_diet"}`.

### FEAT-015 · Category list endpoint · P3 · S
The app's navigation menu needs categories with counts.

Acceptance criteria:
- `GET /api/categories` returns `{"categories": [{"category", "product_count"}]}` sorted by category name, covering every category in the catalogue.

### FEAT-016 · VAT rate on product pages · P3 · S
Business customers want to see the VAT rate before buying.

Acceptance criteria:
- `GET /api/products/<sku>` includes `vat_rate` as a number: 0 for `zero`, 5 for `reduced`, 20 for `standard`.

### FEAT-017 · Remove an item from the cart · P1 · S
There is currently no way to remove something from the basket.

Acceptance criteria:
- `DELETE /api/carts/<id>/items/<sku>` returns 200 with the updated cart; totals are recalculated.
- Removing a SKU that is not in the cart returns 404 `{"error": "item_not_in_cart"}`.

### FEAT-018 · Change item quantity in the cart · P1 · S
Customers want +/- buttons in the basket. While we're in there, the partner's CRM and marketing's abandoned-basket journey have been waiting on basket events since PARTNER-77; the subscription on the bus is already live, commerce just never publishes.

Acceptance criteria:
- `PATCH /api/carts/<id>/items/<sku>` with `{"qty": n}` sets the quantity and returns the updated cart.
- `qty` 0 removes the line. Negative or non-integer `qty` returns 400 `{"error": "invalid_qty"}`.
- Every change to a cart's contents (adding an item, changing a quantity, removing a line) publishes a `cart.updated` event on the bus with the cart id, customer id, lines and repriced total, before the API answers.

### FEAT-019 · Per-line quantity limit · P2 · S
Bulk buyers are emptying the shelves; cap lines at 24 units.

Acceptance criteria:
- Adding or updating a line so that its quantity exceeds 24 returns 400 `{"error": "qty_limit", "max": 24}` and leaves the cart unchanged.

### FEAT-020 · Empty the cart · P2 · S
Customers want a single "clear basket" button.

Acceptance criteria:
- `DELETE /api/carts/<id>/items` removes every line and any coupon, returning the cart with no items and all totals 0.

### FEAT-021 · Item count on the cart · P3 · S
The basket icon needs a badge.

Acceptance criteria:
- The cart response includes `item_count`, the sum of all line quantities.

### FEAT-022 · Total savings on the cart · P3 · S
Marketing wants "You saved £X" in the basket.

Acceptance criteria:
- The cart response includes `savings_pence` = coupon discount plus any multi-buy savings (0 if none).

### FEAT-023 · Request IDs in error bodies · P2 · S
Support asks customers for the error reference but it isn't shown anywhere.

Acceptance criteria:
- Every 4xx and 5xx JSON response from `/api/...` includes `request_id`, equal to the responses `X-Request-Id` header.

### FEAT-024 · JSON 405 responses · P3 · S
Clients calling the wrong method get an HTML page.

Acceptance criteria:
- Calling an existing `/api/...` route with an unsupported method returns 405 with JSON `{"error": "method_not_allowed"}` and an `Allow` header listing the supported methods.

### FEAT-025 · Deep health check · P2 · M
The status page wants to show which subsystems are healthy.

Acceptance criteria:
- `GET /api/healthz/deep` returns 200 `{"ok": bool, "services": {"<name>": bool}}` covering every service edge-api depends on, checked in parallel.
- It responds within 2 seconds even if a dependency is down (that dependency is reported false, `ok` false, status still 200).

### FEAT-026 · Delivery details on order view · P2 · M
Customers can't see when their order arrives.

Acceptance criteria:
- `GET /api/orders/<ref>` includes `delivery: {"date", "window"}` for the booked slot.

### FEAT-027 · Line totals on orders · P3 · S
The order page recomputes line totals in the browser.

Acceptance criteria:
- Every entry in `items` of `GET /api/orders/<ref>` includes `line_total_pence` (unit price × qty), and the order includes `item_count` (sum of quantities).

### FEAT-028 · Change postcode on an account · P2 · M
People move house.

Acceptance criteria:
- `PATCH /api/account/<user_id>` with `{"postcode"}` updates the postcode and returns the account.
- Postcodes that don't map to a delivery zone return 400 `{"error": "invalid_postcode"}`.

### FEAT-029 · Marketing opt-in · P4 · S
We need consent before sending marketing emails.

Acceptance criteria:
- `GET /api/account/<user_id>` includes `marketing_opt_in` (default false); `PATCH /api/account/<user_id>` with `{"marketing_opt_in": true|false}` updates it. Non-boolean values return 400 `{"error": "invalid_opt_in"}`.

### FEAT-030 · Search autocomplete · P2 · M
The search box needs suggestions as you type.

Acceptance criteria:
- `GET /api/search/suggest?q=<prefix>` returns `{"suggestions": [..]}`: up to 8 distinct product names starting with any word that starts with the prefix (case-insensitive), alphabetical.
- Prefixes shorter than 2 characters return an empty list. p95 under 150 ms.

### FEAT-031 · Paginate search results · P2 · S
Popular searches return hundreds of results in one response.

Acceptance criteria:
- `GET /api/search` accepts `page` (default 1) and `per_page` (default 20, max 50) and returns `page`, `per_page` and `total` alongside the results.

### FEAT-032 · Reject very long search queries · P3 · S
Bots send megabyte-long queries.

Acceptance criteria:
- Queries longer than 100 characters return 400 `{"error": "query_too_long"}`.

### FEAT-033 · Normalise search queries · P3 · S
"  APPLES " and "apples" give different results.

Acceptance criteria:
- Search is case-insensitive and ignores leading/trailing/repeated whitespace; equivalent queries return identical results.

### FEAT-034 · Filter delivery slots by date · P3 · S
The slot picker shows one day at a time.

Acceptance criteria:
- `GET /api/delivery/slots?postcode=..&date=YYYY-MM-DD` returns only that date's slots; an invalid date returns 400 `{"error": "invalid_date"}`.

### FEAT-035 · Release a slot hold · P2 · S
Customers who change their mind keep capacity locked for 10 minutes.

Acceptance criteria:
- `DELETE /api/delivery/holds/<hold_id>` returns 204 and the slot's capacity is available again immediately.
- Unknown or already-released holds return 404.

### FEAT-036 · Slot details on a hold · P4 · S
The checkout page re-fetches slots just to show the chosen one.

Acceptance criteria:
- The 201 response of `POST /api/delivery/holds` includes `slot: {"slot_id", "date", "window"}`.

### FEAT-037 · Require a payment token · P2 · S
Checkouts without a token go all the way to the PSP and fail there. Finance also wants bad actors stopped before an order exists, not at the PSP (FRAUD-52).

Acceptance criteria:
- `POST /api/checkout` without `payment_token` returns 400 `{"error": "missing_payment_token"}` without contacting other services.
- Every checkout attempt with a token is pre-screened by the fraud service (`POST /internal/fraud/score`, the same scorer payments uses) before the order is created. A `block` decision returns 402 `{"error": "payment_declined", "reason": "fraud"}` and no order is created. If fraud cannot score the attempt, the checkout is refused with 503 `{"error": "fraud_unavailable"}`; finance does not take unscreened payments.
- The charge-time fraud check in payments stays as it is.

### FEAT-038 · Idempotent checkout · P1 · L
Mobile clients retry checkout on flaky networks and customers get two orders.

Acceptance criteria:
- `POST /api/checkout` honours an `Idempotency-Key` header: repeating a request with the same key returns the same response (same `order_ref`), creating at most one order and one charge.
- Concurrent requests with the same key also produce exactly one order.

### FEAT-039 · Minimum order value · P2 · S
Orders under £10 lose money on delivery. Merchandising doesn't want to just turn those customers away, so the rejection should help them get over the line.

Acceptance criteria:
- Checkouts with goods (after discounts, before delivery) under 1000 pence return 400 `{"error": "below_minimum", "minimum_pence": 1000}`.
- The 400 also carries `suggestions`: up to three products, not already in the basket, that other customers bought with what is in it (the "customers also bought" data recommendations already serves), each with `sku`, `name` and `price_pence`.

### FEAT-040 · Reject expired slot holds at checkout · P2 · M
Orders are confirmed against holds that already expired.

Acceptance criteria:
- Checking out with an expired or released hold returns 409 `{"error": "hold_expired"}` and no charge is made.

### FEAT-041 · Estimated delivery in the checkout response · P3 · S
The confirmation screen shows nothing about delivery.

Acceptance criteria:
- The 201 checkout response includes `estimated_delivery: {"date", "window"}` for the booked slot.

### FEAT-042 · Order status history · P3 · M
Customer service needs to see when an order changed state.

Acceptance criteria:
- `GET /api/orders/<ref>/status-history` returns `{"history": [{"status", "at"}]}` in chronological order, starting with `confirmed`, including `dispatched` (and `cancelled` if cancellation exists).

### FEAT-043 · Reorder · P2 · M
Weekly shoppers rebuild the same basket every week.

Acceptance criteria:
- `POST /api/orders/<ref>/reorder` returns 201 `{"cart_id"}` for a new cart for the same user containing the same SKUs and quantities (items no longer sold are skipped).

### FEAT-044 · Public "also bought" endpoint · P3 · S
The app wants recommendations without loading the full product.

Acceptance criteria:
- `GET /api/products/<sku>/also-bought?limit=n` (default 6, max 12) returns `{"products": [Product]}`.

### FEAT-045 · No out-of-stock recommendations · P3 · M
We recommend things customers can't buy.

Acceptance criteria:
- Products in `also_bought` (product detail) and the also-bought endpoint all have available stock > 0.

### FEAT-046 · Price-appropriate recommendations · P3 · S
Customers looking at a £1 loaf are recommended £14 bottles of wine.

Acceptance criteria:
- `also_bought` never contains a product priced at more than twice the viewed products `price_pence`.
- Products still get recommendations when suitable ones exist (the list is not simply emptied).

### FEAT-047 · Formatted price on products · P4 · S
Every client formats prices differently.

Acceptance criteria:
- Product responses include `price_display`, e.g. `"£2.49"` (pound sign, two decimals, derived from `price_pence`).

### FEAT-048 · List active promotions · P2 · M
The promotions page is hand-edited HTML.

Acceptance criteria:
- `GET /api/promotions` returns `{"promotions": [{"code", "percent_off", "min_spend_pence", "expires"}]}` for every coupon that has not expired; `expires` is `YYYY-MM-DD` or null for coupons that never expire.
- It must work for every coupon in the system (status 200).

### FEAT-049 · Coupon expiry on the cart · P3 · S
Customers want to know how long their discount lasts.

Acceptance criteria:
- When a coupon is applied, the cart includes `coupon_expires` as `YYYY-MM-DD`, or null for coupons that never expire. It is absent or null when no coupon is applied.

### FEAT-050 · Fixed-amount coupon FIVER · P2 · L
Marketing wants "£5 off when you spend £30".

Acceptance criteria:
- A new coupon `FIVER` takes 500 pence off goods when goods total at least 3000 pence (below that: 400 `min_spend`).
- The cart shows `discount_pence` 500, and checkout of such a cart succeeds with the confirmed order total equal to the cart total.

### FEAT-051 · Redeem loyalty points · P3 · L
Points are useless until they can be spent.

Acceptance criteria:
- `POST /api/carts/<id>/loyalty` with `{"points": n}` (multiple of 100, at most the user's balance) takes n pence off the cart (`loyalty_discount_pence`).
- Checkout of that cart succeeds, the order total matches the cart total, and the user's balance drops by n.
- Requires FEAT-005 points to exist; insufficient balance returns 400 `{"error": "insufficient_points"}`.

### FEAT-052 · Loyalty tiers · P4 · S
Bronze, silver, gold.

Acceptance criteria:
- `GET /api/account/<user_id>` includes `tier`: `bronze` under 500 points, `silver` 500-1999, `gold` 2000+.

### FEAT-053 · Cancellation email · P3 · M
Customers who cancel get no confirmation.

Acceptance criteria:
- When an order is cancelled (FEAT-002), the email provider receives one message with template `order_cancelled` and `data.order_ref`.

### FEAT-054 · Tracking link in dispatch email · P3 · S
"Where is my order?" is our top support contact.

Acceptance criteria:
- The `order_dispatched` email's `data` includes `tracking_url` = `https://track.larder.test/<order_ref>`.

### FEAT-055 · Email suppression list · P3 · M
Mail to the old larder-mail.test domain bounces and hurts our sender reputation.

Acceptance criteria:
- No email is sent to addresses at `larder-mail.test`; each suppressed message is logged and counted, and other customers are unaffected.

### FEAT-056 · Order cancelled webhook · P3 · M
The partner keeps picking cancelled orders.

Acceptance criteria:
- When an order is cancelled (FEAT-002) the partner receives one `order.cancelled` event with `order_ref`.

### FEAT-057 · Open orders on the account summary · P3 · S
The account page should show how many orders are on their way.

Acceptance criteria:
- `GET /api/account/<user_id>` includes `open_orders`: the number of the users orders that are confirmed and not yet dispatched (or cancelled).

### FEAT-058 · Webhook stats endpoint · P4 · S
On-call wants to see webhook health without reading files.

Acceptance criteria:
- `GET /internal/webhooks/stats` on the webhooks service returns `{"queue_depth", "delivered_total", "failed_attempts_total"}` as integers.

### FEAT-059 · Pick lists by date · P3 · M
The warehouse prints pick lists from a spreadsheet.

Acceptance criteria:
- `GET /internal/picklists?date=YYYY-MM-DD` on warehouse returns `{"date", "lines": [{"sku", "qty"}]}`: total quantities per SKU for confirmed, undispatched orders delivering that date.

### FEAT-060 · Don't dispatch cancelled orders · P2 · S
Cancelled orders are still dispatched.

Acceptance criteria:
- `POST /internal/dispatch/<ref>` for a cancelled order returns 409 `{"error": "order_cancelled"}` and no `order.dispatched` event is published.

### FEAT-061 · Restock endpoint · P2 · M
Stock is only ever loaded at startup.

Acceptance criteria:
- `POST /internal/inventory/<sku>/restock` on inventory with `{"qty": n}` (n > 0) increases on-hand by n and returns the new `{sku, on_hand, reserved, available}`.
- Concurrent restocks and reservations never lose an update.

### FEAT-062 · Low-stock report · P3 · S
Buyers want a daily low-stock list.

Acceptance criteria:
- `GET /internal/inventory/low-stock?threshold=n` (default 10) returns `{"items": [{"sku", "available"}]}` for every SKU with available ≤ n, lowest first.

### FEAT-063 · Public stock endpoint · P3 · S
The app polls product detail just for stock.

Acceptance criteria:
- `GET /api/products/<sku>/stock` returns `{"sku", "available"}`; unknown SKU 404.

### FEAT-064 · Payments by order · P3 · S
Finance looks up payments by order reference.

Acceptance criteria:
- `GET /internal/payments/orders/<order_ref>` on payments returns `{"order_ref", "charges": [{"charge_id", "amount_pence", "at"}], "refunds": [...]}`.

### FEAT-065 · Refund API · P2 · M
Refunds are done by hand in the PSP dashboard.

Acceptance criteria:
- `POST /internal/payments/refunds` on payments with `{"order_ref", "amount_pence"}` refunds through the PSP and returns 201 `{"refund_id"}`.
- Refunds can never exceed what was captured for the order (409 `{"error": "exceeds_captured"}`), and retrying the same request does not refund twice.

### FEAT-066 · Fraud rule listing · P4 · S
Risk wants to see which rules fire.

Acceptance criteria:
- `GET /internal/fraud/rules` on fraud returns `{"rules": [{"name", "hits"}]}` with hit counts since startup.

### FEAT-067 · Daily ledger totals · P2 · M
Finance closes the books from the ledger.

Acceptance criteria:
- `GET /internal/ledger/daily?date=YYYY-MM-DD` on ledger returns `{"date", "captured_pence", "refunded_pence", "net_pence"}` that agree with the PSP's records for that day.

### FEAT-068 · Ledger refund entries · P3 · M
Refunds never reach the ledger.

Acceptance criteria:
- Every refund (FEAT-002 or FEAT-065) creates a ledger entry with negative amount and the order_ref.

### FEAT-069 · Public VAT rates · P4 · S
The web app hard-codes VAT rates.

Acceptance criteria:
- `GET /api/tax/rates` returns `{"zero": 0, "reduced": 5, "standard": 20}`, served from the tax services rates.

### FEAT-070 · Delivery VAT in totals · P4 · S
Finance needs VAT on delivery separated out.

Acceptance criteria:
- The tax totals response includes `delivery_vat_pence` = round-half-up(delivery_fee_pence × 20 / 120).

### FEAT-071 · Event bus lag per topic · P3 · S
On-call can see how many events are queued but not how old they are.

Acceptance criteria:
- Each topic in `GET /internal/bus/topics` on the bus includes `oldest_pending_age_s`: seconds the oldest undelivered event on that topic has been waiting (0 when nothing is pending).

### FEAT-072 · Event bus dead-letter queue · P2 · L
One bad message can wedge a topic forever.

Acceptance criteria:
- A delivery that fails 5 times for a subscriber is moved to a dead-letter list and stops blocking later events for that subscriber and topic.
- `GET /internal/bus/dead-letters` lists them with `{"topic", "subscriber", "key", "attempts", "last_error"}`.

### FEAT-073 · Adjust slot capacity · P3 · S
Ops add vans on busy days.

Acceptance criteria:
- `PATCH /internal/slots/<slot_id>` on slots with `{"capacity": n}` sets capacity (n ≥ current bookings, else 409 `{"error": "below_booked"}`).

### FEAT-074 · Hide full delivery slots · P3 · S
Customers keep clicking slots that are already full.

Acceptance criteria:
- `GET /api/delivery/slots` returns only slots with available capacity, unless `include_full=true` is passed, in which case full slots are included as today.

### FEAT-075 · Customer registration · P1 · M
New customers are added by a nightly import.

Acceptance criteria:
- `POST /api/account` with `{"name", "email", "postcode"}` returns 201 `{"user_id"}`, and the new user can create carts and check out.
- Duplicate email (case-insensitive) returns 409 `{"error": "email_taken"}`; invalid email or postcode returns 400 `{"error": "invalid_email" | "invalid_postcode"}`.

### FEAT-076 · Abandoned cart expiry · P3 · M
Old carts pile up forever.

Acceptance criteria:
- Carts untouched for 24 hours expire; reading or modifying them returns 404 `{"error": "cart_expired"}`. Active carts are unaffected.

### FEAT-077 · Merge carts · P3 · M
Logged-out baskets get lost when people log in.

Acceptance criteria:
- `POST /api/carts/<id>/merge` with `{"from_cart_id"}` adds the other cart's lines (summing quantities, respecting limits) and deletes the source cart.

### FEAT-078 · "Did you mean" for searches · P3 · M
Typos return nothing.

Acceptance criteria:
- When a search returns no results, the response includes `suggestion`, a product-name word within edit distance 1 of a query word, if one exists (otherwise null).

### FEAT-079 · CORS for the new web app · P2 · S
The new web app runs on www.larder.test.

Acceptance criteria:
- `/api/*` responses include `Access-Control-Allow-Origin: https://www.larder.test` when the request Origin is that value, and preflight `OPTIONS` requests return 204 with allowed methods.

### FEAT-080 · API version endpoint · P4 · S
Clients need to know which API they're talking to.

Acceptance criteria:
- `GET /api/version` returns `{"api_version": "2026.1"}`.

### FEAT-081 · Gzip responses · P3 · S
Mobile data costs.

Acceptance criteria:
- Responses over 1 KB are gzip-compressed when the request sends `Accept-Encoding: gzip`, with `Content-Encoding: gzip`.

### FEAT-082 · ETags on product detail · P3 · M
The app re-downloads unchanged products.

Acceptance criteria:
- `GET /api/products/<sku>` returns an `ETag`; a request with a matching `If-None-Match` returns 304 with an empty body.

### FEAT-083 · Server-Timing header · P4 · S
Frontend wants to see backend time in the browser dev tools.

Acceptance criteria:
- Every `/api/...` response includes a `Server-Timing` header of the form `app;dur=<milliseconds>` (a non-negative number).

### FEAT-084 · Cart price breakdown · P3 · M
Customers dispute totals.

Acceptance criteria:
- `GET /api/carts/<id>/price-breakdown` returns per line `{"sku", "qty", "unit_price_pence", "line_total_pence", "discount_allocated_pence"}` plus the cart totals; allocations add up to `discount_pence` exactly.

### FEAT-085 · Prices in euros · P4 · S
Irish customers want euro estimates.

Acceptance criteria:
- `GET /api/carts/<id>?currency=EUR` adds `total_eur_cents` = round-half-up(total_pence × 1.17); other currencies return 400 `{"error": "unsupported_currency"}`.

### FEAT-086 · Eligible coupons for a cart · P3 · M
Customers don't know which codes they can use.

Acceptance criteria:
- `GET /api/carts/<id>/eligible-coupons` returns `{"coupons": [code]}`: unexpired coupons whose minimum spend the cart meets.

### FEAT-087 · Order counts by status · P4 · S
The ops dashboard wants a status summary.

Acceptance criteria:
- `GET /internal/orders/stats` on orders returns `{"<status>": count}` for every status present.

### FEAT-088 · Delivery notes · P3 · M
"Leave with neighbour" notes are lost.

Acceptance criteria:
- `POST /api/checkout` accepts `delivery_note` (≤ 200 characters, else 400 `{"error": "note_too_long"}`); it appears on `GET /api/orders/<ref>`.

### FEAT-089 · Driver tips · P3 · L
Customers keep asking how to tip drivers.

Acceptance criteria:
- `POST /api/checkout` accepts `tip_pence` (0-1000, else 400 `{"error": "invalid_tip"}`); the tip is charged with the order and shown as `tip_pence` on the order, and `total_pence` includes it.
- Tips carry no VAT and are not discounted; orders without a tip are unchanged.

### FEAT-090 · Personal data export · P2 · M
GDPR subject access requests are done by hand.

Acceptance criteria:
- `GET /api/account/<user_id>/export` returns `{"account", "orders": [...], "loyalty": {"points"}}` with every order the user placed.

### FEAT-091 · Account deletion · P2 · M
GDPR erasure requests are done by hand.

Acceptance criteria:
- `DELETE /api/account/<user_id>` returns 204; afterwards the account shows name `"deleted"` and email null, and orders are retained.
- Deleted accounts cannot create carts (403 `{"error": "account_deleted"}`).

### FEAT-092 · Notification history · P4 · S
Support can't tell whether an email went out.

Acceptance criteria:
- `GET /internal/notifications?order_ref=<ref>` on notifications returns `{"messages": [{"template", "to", "sent_at", "status"}]}`.

### FEAT-093 · Driver on dispatched orders · P3 · S
Customers want to know who is delivering.

Acceptance criteria:
- `POST /internal/dispatch/<ref>` on warehouse accepts an optional JSON body `{"driver_id"}`; when given it is stored on the order and shown as `driver_id` on `GET /api/orders/<ref>` (null when not given).

### FEAT-094 · Delivery zones endpoint · P4 · S
The website shows a hard-coded list of areas.

Acceptance criteria:
- `GET /api/delivery/zones` returns `{"zones": [{"zone", "name", "postcodes": [..]}]}` for all zones.

### FEAT-095 · Filter products by tag · P3 · S
The 3-for-2 landing page needs a product list.

Acceptance criteria:
- `GET /api/products?tag=3for2` returns only products with that tag; `total` reflects the filtered count.

### FEAT-096 · In-stock filter · P3 · M
Customers hate clicking sold-out products.

Acceptance criteria:
- `GET /api/products?in_stock=true` returns only products with available stock > 0, with correct `total` and pagination.

### FEAT-097 · Top search queries · P4 · S
Merchandising wants to know what people search for.

Acceptance criteria:
- `GET /internal/search/top-queries?limit=n` on search returns `{"queries": [{"query", "count"}]}` (normalised queries, most frequent first, since startup).

### FEAT-098 · Service info endpoints · P4 · M
Platform wants a uniform info endpoint on every service.

Acceptance criteria:
- Every one of the 21 services serves `GET /internal/info` returning `{"service", "repo", "uptime_s"}`.

### FEAT-099 · Product reviews (read-only) · P3 · L
We imported reviews from the old site into a CSV nobody serves.

Acceptance criteria:
- `POST /api/products/<sku>/reviews` with `{"user_id", "rating" (1-5), "text" (≤ 500 chars)}` returns 201; `GET /api/products/<sku>/reviews` lists them newest first.
- Product detail includes `rating_avg` (one decimal, null if none) and `rating_count`. Invalid rating/text return 400.

### FEAT-100 · Favourites · P3 · M
Customers want a favourites list.

Acceptance criteria:
- `PUT /api/account/<user_id>/favourites/<sku>` adds (idempotent), `DELETE` removes, `GET /api/account/<user_id>/favourites` returns `{"products": [Product]}` in the order added.

---

## Operational tickets

### REL-1 · "Prices changed" error at checkout · P2
Support: "A few customers a day say checkout told them 'prices have changed, please review your basket',
but nothing changed. Seems to happen more with discount codes." No repro yet.

### REL-2 · Delivery slot page hangs · P2
On-call: "Every so often the slot picker spins forever and checkouts start failing. Restarting fulfilment
fixes it for a while. We've been doing that every morning."

### REL-3 · Double charges · P2
Finance: "Reconciliation found customers charged twice for one order. Seems to cluster on days the PSP
had a bad time. Refunding manually."

### REL-4 · Overselling promo items · P3
Warehouse: "We keep getting orders for promo items we don't have. Stock in the system says we did."

### REL-5 · Search sometimes very slow · P3
"Search is usually fine but sometimes it takes several seconds, and the product pages hang at the
same time."

---

## Tech debt

### TD-1 · Move storefront money to integer pence · P3
The storefront still stores and computes prices as float pounds. Everything new is in pence. Nobody has
had time.

### TD-2 · Replace the deprecated `request` library in fulfilment · P4
`request` has been deprecated since 2020. It is still used by the PSP adapter.
