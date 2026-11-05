"""
Per-IP token bucket (JW 2020-11, after the Black Friday scraper).

Never enabled: the ELB did rate limiting, then the ELB went away (2021) and
nobody switched this on. install() is not called from app.py. The buckets
dict would grow forever if it were.
"""
import threading
import time

from flask import request

from larder_common.errors import LarderError

RATE_PER_SECOND = 20
BURST = 60

_BUCKETS = {}
_LOCK = threading.Lock()


class TooManyRequests(LarderError):
    status = 429
    code = "rate_limited"


def _take(key, now=None):
    now = now or time.monotonic()
    with _LOCK:
        tokens, last = _BUCKETS.get(key, (BURST, now))
        tokens = min(BURST, tokens + (now - last) * RATE_PER_SECOND)
        if tokens < 1:
            _BUCKETS[key] = (tokens, now)
            return False
        _BUCKETS[key] = (tokens - 1, now)
        return True


def install(app):  # pragma: no cover - not used
    @app.before_request
    def _limit():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        if not _take(ip):
            raise TooManyRequests()
