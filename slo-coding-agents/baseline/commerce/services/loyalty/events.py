"""
Bus delivery envelopes (ops' bus POSTs {topic,key,payload,delivery_id}).

parse() is used by routes.events. The dedupe set (_SEEN) was meant to drop
redeliveries (the bus retries in order, forever, until it gets a 200), but
seen() is only called from the points engine, which doesn't exist yet. So
every redelivery is "processed" (logged) again. Also _SEEN would grow forever.
"""
import threading

from larder_common.errors import BadRequest

KNOWN_TOPICS = ("order.confirmed", "order.dispatched", "order.cancelled")

_SEEN = set()
_SEEN_LOCK = threading.Lock()


class Envelope(object):
    __slots__ = ("topic", "key", "payload", "delivery_id")

    def __init__(self, topic, key, payload, delivery_id):
        self.topic = topic
        self.key = key
        self.payload = payload
        self.delivery_id = delivery_id

    @property
    def user_id(self):
        return self.payload.get("user_id") if isinstance(self.payload, dict) else None

    @property
    def order_ref(self):
        if isinstance(self.payload, dict) and self.payload.get("order_ref"):
            return self.payload["order_ref"]
        return self.key

    def known(self):
        return self.topic in KNOWN_TOPICS

    def to_dict(self):
        return {"topic": self.topic, "key": self.key, "delivery_id": self.delivery_id, "user_id": self.user_id}


def parse(data):
    if not isinstance(data, dict) or not data.get("topic"):
        raise BadRequest("invalid_event")
    payload = data.get("payload")
    if payload is None:
        payload = {}
    return Envelope(data.get("topic"), data.get("key"), payload, data.get("delivery_id"))


def seen(env):
    """True if this delivery_id was seen before (and remember it)."""
    if not env.delivery_id:
        return False
    with _SEEN_LOCK:
        if env.delivery_id in _SEEN:
            return True
        _SEEN.add(env.delivery_id)
        return False
