"""
Slot hold expiry on carts (SB 2021-05).

Slots (ops) gives us an expires_at with every hold. HOLD_MINUTES is our own
guess at it from before slots told us (FIXME(SB 2021-05-20): fulfilment
should tell us this - it does now, we still use the guess in hold_expired()
when expires_at is missing).

Checkout does NOT check this; an expired hold is sent to orders anyway and
ops decides. Only the cart's debug view uses it.
"""
import datetime

from larder_common.helpers.dates import parse_iso, utcnow

HOLD_MINUTES = 20


def hold_expired(st, now=None):
    if not st.hold_id:
        return False
    now = now or utcnow()
    exp = parse_iso(st.hold_expires_at)
    if exp is None:
        updated = parse_iso(st.updated_at) or now
        exp = updated + datetime.timedelta(minutes=HOLD_MINUTES)
    return exp < now
