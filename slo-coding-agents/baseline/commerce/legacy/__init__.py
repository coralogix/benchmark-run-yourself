"""
The 2016-2018 storefront code. Some of this is still live:

    pricing_v1     - the pricing engine production actually runs (see settings_local)
    promo_dates    - expiry parsing for pre-2019 coupons (promotions)
    v1_routes      - /v1 API, mounted by edge-api
    rounding       - pricing_v1's helpers

the rest is kept "until we're sure nothing calls it". It moved from
larder/legacy/ to the repo root in the 2023 split because three services
import it and nobody wanted it in libs/.
"""
