"""
Timing helpers (statsd era). See utils.timed for the *other* one, which logs
only over 500ms; this one logs over SLOW_MS (250) and also feeds the statsd
shim (a no-op since 2024).
"""
import contextlib
import functools
import time

from larder_common.legacy_bits import statsd_shim
from larder_common.log import get_logger

log = get_logger("timing")

SLOW_MS = 250


@contextlib.contextmanager
def timer(label, slow_ms=SLOW_MS):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        statsd_shim.timing(label, ms)
        if ms > slow_ms:
            log.info("%s took %.0fms", label, ms)


def timed(label=None, slow_ms=SLOW_MS):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with timer(label or fn.__name__, slow_ms):
                return fn(*a, **kw)

        return wrapper

    return deco
