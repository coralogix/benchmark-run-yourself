"""
Checkout.

    1. load the cart (cart service) and check it's open
    2. price it with pricing (whatever engine pricing runs)
    3. look up the customer's email (accounts)
    4. write a pending checkout row locally
    5. POST /internal/orders to ops (reserve stock, confirm slot, take payment)
    6. map the result, run order_confirmed hooks

ops re-validates the total; if we disagree it answers 409 price_mismatch
(customers see "prices changed").
"""
import time

from larder_common import settings, telemetry, utils
from larder_common.errors import BadRequest, Conflict, LarderError
from larder_common.log import get_logger
from services.checkout import hooks, orders_client, outcomes, payload, peers, tokens, totals
from services.checkout.models import Checkout

log = get_logger("checkout")

# checkouts currently in flight, by order_ref -> start time. For /_inflight.
# never cleaned up if the thread dies mid-request (it doesn't, usually).
INFLIGHT = {}

# log a warning when the orders call takes longer than this (seconds)
SLOW_ORDERS_CALL = 5.0


class CheckoutError(LarderError):
    pass


def new_order_ref():
    return utils.gen_ref(settings.ORDER_REF_PREFIX, 10)


def _save_pending(order_ref, cart, breakdown, hold_id):
    coupon = breakdown.get("coupon") or {}
    return Checkout.create(
        order_ref=order_ref,
        cart_id=cart["cart_id"],
        user_id=cart.get("user_id"),
        status="pending",
        subtotal=breakdown.get("subtotal"),
        discount=breakdown.get("discount"),
        delivery_fee=breakdown.get("delivery_fee"),
        total=breakdown.get("total"),
        total_pence=breakdown["total_pence"],
        coupon_code=coupon.get("code") or cart.get("coupon"),
        hold_id=hold_id,
        engine=breakdown.get("engine"),
        attempts=1,
    )


def _fail(record, status, outcome, code, extra=None):
    record.status = "failed"
    record.error = code
    record.touch()
    telemetry.record_checkout(outcome)
    raise CheckoutError(code, status=status, **(extra or {}))


def _validate_request(cart_id, payment_token):
    if not cart_id:
        raise BadRequest("cart_id_required")
    if not payment_token:
        raise BadRequest("payment_token_required")
    if not tokens.looks_valid(payment_token):
        log.info("odd looking payment token for cart %s: %s", cart_id, tokens.mask(payment_token))


def checkout(cart_id, hold_id, payment_token):
    if not cart_id:
        raise BadRequest("cart_id_required")
    cart = peers.get_cart(cart_id)
    if cart.get("status") != "open":
        raise Conflict("cart_closed")
    hold_id = hold_id or cart.get("hold_id")
    if not hold_id:
        raise BadRequest("hold_required")
    _validate_request(cart_id, payment_token)

    lines = cart.get("lines") or []
    if not lines:
        raise BadRequest("empty_cart")
    breakdown = peers.price(lines, cart.get("coupon"))
    if not breakdown.get("lines"):
        raise BadRequest("empty_cart")

    email = peers.user_email(cart.get("user_id"))
    order_ref = new_order_ref()
    totals.check(order_ref, breakdown)
    record = _save_pending(order_ref, cart, breakdown, hold_id)
    body = payload.build_order_body(order_ref, cart, breakdown, email, hold_id, payment_token)

    INFLIGHT[order_ref] = time.time()
    t0 = time.time()
    try:
        res = orders_client.create_order(body)
    except Exception as e:
        log.error("create_order failed for %s after retries: %s", order_ref, e)
        _fail(record, *outcomes.UNAVAILABLE)
    finally:
        INFLIGHT.pop(order_ref, None)
        took = time.time() - t0
        if took > SLOW_ORDERS_CALL:
            log.warning("orders call for %s took %.1fs", order_ref, took)

    if outcomes.is_confirmed(res):
        record.status = "confirmed"
        record.charge_id = res.body.get("charge_id")
        if res.body.get("total_pence") is not None:
            record.total_pence = res.body["total_pence"]
        record.touch()
        telemetry.record_checkout(outcomes.CONFIRMED)
        log.info("order %s confirmed total_pence=%s", order_ref, record.total_pence)
        hooks.run("order_confirmed", checkout=record, cart=cart, breakdown=breakdown, body=body)
        return {
            "order_ref": order_ref,
            "status": "confirmed",
            "total_pence": record.total_pence,
            "total": record.total,
        }

    mapped = outcomes.map_error(res)
    if mapped is None:
        log.error("unexpected orders response for %s: %r", order_ref, res)
        _fail(record, *outcomes.UNKNOWN)
    status, outcome, code = mapped
    if code == "price_mismatch":
        log.warning(
            "price mismatch on %s: sent %s, orders expected %s (engine %s)",
            order_ref,
            body["total_pence"],
            res.body.get("expected_total_pence"),
            breakdown.get("engine"),
        )
    extra = {"sku": res.body["sku"]} if code == "out_of_stock" and res.body.get("sku") else None
    _fail(record, status, outcome, code, extra)


def get_checkout(order_ref):
    return Checkout.get(order_ref)


def inflight():
    now = time.time()
    return {ref: round(now - t, 2) for ref, t in list(INFLIGHT.items())}
