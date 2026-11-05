"""
Coupons.

Coupon rows come from three generations of marketing tooling, so the
``expires`` column is free text. parse_expiry() deals with that.

Lifted from the storefront's larder/coupons.py in the 2023 split. The cart
bits (apply_to_cart / remove_from_cart) stayed behind in the cart service;
this module only answers "is this code usable on a basket of N pence".
"""
import datetime
import re

from dateutil import parser as dateutil_parser

from larder_common import settings, telemetry
from larder_common.errors import CouponError
from larder_common.log import get_logger
from services.promotions.models import Coupon

log = get_logger("coupons")

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# codes that are never shown in the UI but still accepted (staff, partners)
HIDDEN_CODES = {"STAFF25", "PARTNER10"}

# AS 2021-03: max length of a code the campaign API will send us
MAX_CODE_LEN = 32


def normalise_code(code):
    code = (code or "").strip()[:MAX_CODE_LEN]
    if not settings.COUPON_CASE_SENSITIVE:
        code = code.upper()
    return code


def lookup(code):
    code = normalise_code(code)
    if not code:
        return None
    c = Coupon.get(code)
    if c is None or not c.active:
        return None
    return c


def list_visible():
    return [c for c in Coupon.filter(active=1, order_by="code") if c.code not in HIDDEN_CODES]


def parse_expiry(raw):
    """
    Returns an aware datetime (UTC) or None for "doesn't expire".

    Handles:
        2027-06-30              (merch tool, 2019+)
        2027-04-30T23:59:59Z    (campaign API, 2021+)
        anything else           -> legacy promo spreadsheet formats
    """
    if raw is None or raw == "":
        return None
    raw = str(raw).strip()
    if _ISO_DAY.match(raw):
        d = datetime.datetime.strptime(raw, "%Y-%m-%d")
        # valid until the end of the day
        return d.replace(hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc)
    if "T" in raw:
        dt = dateutil_parser.isoparse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    # pre-2019 codes were bulk loaded from the promo spreadsheet
    from legacy.promo_dates import parse_promo_date

    return parse_promo_date(raw)


def is_expired(coupon, now=None):
    exp = parse_expiry(coupon.expires)
    if exp is None:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return exp < now


def validate(coupon, subtotal):
    """Raise CouponError if the coupon can't be used on a cart of `subtotal` pounds."""
    if is_expired(coupon):
        raise CouponError("expired_coupon")
    if coupon.min_spend and subtotal < coupon.min_spend:
        raise CouponError("min_spend", min_spend_pence=int(round(coupon.min_spend * 100)))
    return True


def check_code(code, subtotal_pence):
    """The /validate endpoint. Returns the Coupon or raises CouponError.

    subtotal arrives in pence (pricing sends subtotal_pence) and is compared
    against min_spend in pounds, as it always has been.
    """
    coupon = lookup(code)
    if coupon is None:
        telemetry.record_coupon("invalid")
        raise CouponError("invalid_coupon")
    subtotal = (subtotal_pence or 0) / 100.0
    try:
        validate(coupon, subtotal)
    except CouponError as e:
        telemetry.record_coupon("expired" if e.code == "expired_coupon" else e.code)
        raise
    telemetry.record_coupon("applied")
    log.info("coupon %s ok for subtotal %sp", coupon.code, subtotal_pence)
    return coupon


def describe(coupon):
    if coupon.free_delivery:
        return "Free delivery"
    pct = coupon.percent_off
    if pct == int(pct):
        pct = int(pct)
    return "%s%% off" % pct


def expiry_display(coupon):
    """For the marketing export. NB: blows up on the same rows validate() does."""
    exp = parse_expiry(coupon.expires)
    return exp.strftime("%d %b %Y") if exp else "No expiry"
