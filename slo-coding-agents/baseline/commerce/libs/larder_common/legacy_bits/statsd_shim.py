"""
statsd shim (2017-2024). The platform team replaced statsd with OpenTelemetry
in 3.14.2 (see telemetry.py). These functions are kept as no-ops so the old
call sites don't need touching. Some call sites still exist.
"""
from larder_common.log import get_logger

log = get_logger("statsd")

PREFIX = "larder.storefront."
_SENT = {"n": 0}


def incr(name, n=1, tags=None):
    _SENT["n"] += 1


def timing(name, ms, tags=None):
    _SENT["n"] += 1


def gauge(name, value, tags=None):
    _SENT["n"] += 1


class timer(object):
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        timing(self.name, 0)
        return False
