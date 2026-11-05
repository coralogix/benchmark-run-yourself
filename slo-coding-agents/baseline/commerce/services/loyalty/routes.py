"""
loyalty routes.

    POST /internal/events           bus deliveries {topic,key,payload,delivery_id} -> 200
    GET  /internal/loyalty/<user>   {user_id, points}   (always 0, see points.py)
"""
import threading

from flask import Blueprint

from larder_common.log import get_logger
from larder_common.web import body, ok
from services.loyalty import events as bus_events
from services.loyalty import points

log = get_logger("loyalty")

bp = Blueprint("loyalty", __name__)

# delivery counters by topic, for /internal/loyalty/_stats. process lifetime.
_RECEIVED = {}
_RECEIVED_LOCK = threading.Lock()


@bp.route("/internal/events", methods=["POST"])
def events():
    env = bus_events.parse(body())
    topic = env.topic
    if not env.known():
        log.warning("unknown topic %s from the bus, acking anyway", topic)
    with _RECEIVED_LOCK:
        _RECEIVED[topic] = _RECEIVED.get(topic, 0) + 1
    # TODO(PM-1142) points engine: credit points for order.confirmed here
    log.info(
        "bus delivery %s topic=%s key=%s user=%s (ignored, points engine not built)",
        env.delivery_id,
        topic,
        env.key,
        env.user_id,
    )
    return ok({"ok": True})


@bp.route("/internal/loyalty/_stats")
def stats():
    with _RECEIVED_LOCK:
        return ok({"received": dict(_RECEIVED)})


@bp.route("/internal/loyalty/<user_id>")
def loyalty(user_id):
    return ok({"user_id": user_id, "points": points.balance(user_id)})
