"""
Talking to ops' orders service.

Moved from larder/clients/fulfilment.py in the 2023 split. Uses the shared
larder_common.http client like everything else, so it inherits its defaults
(no timeout, retries; see larder_common/http.py + settings.py).

Retrying the create is safe: orders de-duplicates on order_ref, and we send
the same order_ref on every attempt, so a retried create just returns the
order that was already created. (JW 2018, when fulfilment was a PHP app)
"""
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("checkout.orders")

ORDERS = "orders"


def create_order(body):
    """POST /internal/orders. Returns a ServiceResponse; raises if every attempt failed."""
    log.info("creating order %s total_pence=%s", body.get("order_ref"), body.get("total_pence"))
    return client(ORDERS).post("/internal/orders", json=body)


def get_order(order_ref):
    return client(ORDERS).get("/internal/orders/%s" % order_ref)
