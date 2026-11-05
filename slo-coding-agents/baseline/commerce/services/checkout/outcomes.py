"""
ops orders response -> (our status, metric outcome, error code).

Labels for commerce_checkouts{outcome} are part of the dashboards contract
(platform team), so don't rename them. "declined" is not "payment_declined"
on purpose (the 2019 statsd name).
"""

ERROR_MAP = {
    (409, "price_mismatch"): (409, "price_mismatch", "price_mismatch"),
    (409, "out_of_stock"): (409, "out_of_stock", "out_of_stock"),
    (402, "payment_declined"): (402, "declined", "payment_declined"),
    (502, "payment_error"): (502, "payment_error", "payment_error"),
}

CONFIRMED = "created"
UNKNOWN = (502, "error", "upstream_error")
UNAVAILABLE = (502, "error", "orders_unavailable")


def is_confirmed(res):
    if res.status == 201:
        return True
    # 2019: fulfilment answered 200 for confirmed orders for a while
    return res.ok and isinstance(res.body, dict) and res.body.get("status") == "confirmed"


def map_error(res):
    err = (res.body or {}).get("error") if isinstance(res.body, dict) else None
    return ERROR_MAP.get((res.status, err))
