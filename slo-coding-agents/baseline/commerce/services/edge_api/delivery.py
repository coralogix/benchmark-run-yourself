"""
Delivery slots: postcode -> zone -> slots, and slot holds. Zones and slots
belong to ops' slots service since 2023.

FALLBACK: if the slots service errors on the zone lookup (not a 404, an
actual error), we fall back to the zone table in seed/zones.json, which is the
2019 zone map. SB 2023-06: "slots was flaky during the migration, customers
couldn't even see the slot page". The fallback is still on; the 2019 map is
missing the zones added since.

PM-1712 (TH 2024-02): bots were walking /api/delivery/slots with made-up
postcodes and slots was logging thousands of 404s. Postcodes that aren't in our
zone table now get unknown_postcode without asking slots. (The table has been
kept in step with slots' by hand since, when new postcodes go live.)
"""
import threading

from larder_common.errors import BadRequest, Conflict, NotFound, UpstreamError
from larder_common.helpers.strings import clean_postcode
from larder_common.log import get_logger
from larder_common.seedfiles import load_json
from larder_common.utils import normalise_postcode
from services.edge_api import upstreams

log = get_logger("edge.delivery")

# FIXME(SB 2021-05-20): slots should tell us this (it does now: expires_at)
HOLD_MINUTES = 20

_FALLBACK_ZONES = None
_FALLBACK_LOCK = threading.Lock()


def _fallback_zone(postcode):
    global _FALLBACK_ZONES
    with _FALLBACK_LOCK:
        if _FALLBACK_ZONES is None:
            table = {}
            for z in load_json("zones.json"):
                for pc in z.get("postcodes", []):
                    table[pc.upper()] = z["zone"]
            _FALLBACK_ZONES = table
    return _FALLBACK_ZONES.get(postcode)


def _lookup(pc):
    try:
        return upstreams.zone_lookup(pc)
    except UpstreamError as e:
        zone = _fallback_zone(pc)
        log.warning("zone lookup for %s failed (%s), fallback table says %s", pc, e.code, zone)
        return zone


def resolve_zone(postcode):
    pc = normalise_postcode(postcode)
    if not pc:
        raise BadRequest("postcode_required")
    if _fallback_zone(pc) is None:
        # people type "LD11AA"; try the stored format with the space put back
        squashed = clean_postcode(pc)
        if len(squashed) > 3:
            pc = squashed[:-3] + " " + squashed[-3:]
    if _fallback_zone(pc) is None:
        raise NotFound("unknown_postcode")  # PM-1712
    zone = _lookup(pc)
    if zone is None:
        raise NotFound("unknown_postcode")
    return zone


def list_slots(postcode):
    zone = resolve_zone(postcode)
    try:
        raw = upstreams.slots(zone)
    except Exception as e:
        log.error("slot lookup failed for %s: %s", zone, e)
        raise UpstreamError("slots_unavailable")
    slots = []
    for s in raw:
        capacity = s.get("capacity") or 0
        booked = s.get("booked") or 0
        slots.append(
            {
                "slot_id": s.get("slot_id"),
                "date": s.get("date"),
                "window": s.get("window"),
                "available": (capacity - booked) > 0,
            }
        )
    return {"zone": zone, "slots": slots}


def hold(cart_id, slot_id):
    if not cart_id:
        raise BadRequest("cart_id_required")
    cart = upstreams.cart_lines(cart_id)  # 404 cart_not_found passes through
    if cart.get("status") != "open":
        raise Conflict("cart_closed")
    if not slot_id:
        raise BadRequest("slot_id_required")
    res = upstreams.hold_slot(slot_id, cart_id)
    if res.status == 409:
        raise Conflict("slot_full")
    if res.status == 404:
        raise NotFound("unknown_slot")
    if not res.ok:
        log.error("slot hold failed: %r", res)
        raise UpstreamError("hold_failed")
    hold_id = res.body.get("hold_id")
    expires_at = res.body.get("expires_at")
    # remember it on the cart so checkout can find it without hold_id
    upstreams.set_hold(cart_id, hold_id, slot_id, expires_at)
    return {"hold_id": hold_id, "expires_at": expires_at}
