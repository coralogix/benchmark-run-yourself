"""Default order_confirmed hooks (see settings.CHECKOUT_HOOKS). Order matters."""
import json

from larder_common import settings
from larder_common.errors import UpstreamError
from larder_common.log import get_logger
from services.checkout import peers
from services.checkout.models import CheckoutEvent

log = get_logger("checkout.order_hooks")


def record_checkout_event(checkout, cart, breakdown, **_):
    CheckoutEvent.create(
        order_ref=checkout.order_ref,
        event="confirmed",
        payload=json.dumps(
            {"total_pence": checkout.total_pence, "engine": breakdown.get("engine"), "coupon": checkout.coupon_code}
        ),
    )


def close_cart(checkout, cart, breakdown, **_):
    # the cart service marks the cart checked_out so it can't be checked out twice.
    res = peers.close_cart(checkout.cart_id, checkout.order_ref)
    if not res.ok:
        log.error("could not close cart %s for %s: %r", checkout.cart_id, checkout.order_ref, res)
        raise UpstreamError("cart_close_failed")


def crm_sync(checkout, cart, breakdown, **_):
    if not settings.CRM_ENABLED:
        return
    # the CRM integration lives in legacy/crm.py (disabled since 2021)
    from legacy import crm

    class _O(object):
        order_ref = checkout.order_ref
        user_id = checkout.user_id
        total = checkout.total

    crm.push_order(_O())
