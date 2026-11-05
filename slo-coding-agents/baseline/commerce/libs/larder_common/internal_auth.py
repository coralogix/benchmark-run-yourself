"""
X-Internal-Key checking for /internal routes (2018).

The key check was "temporarily" downgraded to a warning during the 2021 mesh
migration, because the mesh strips unknown headers on some routes (it doesn't
any more). So this decorator only logs. It is still applied to some routes and
not others; there is no pattern.
"""
import functools

from flask import request

from larder_common.log import get_logger

log = get_logger("internal_auth")

_warned = set()


def internal_only(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        from larder_common import settings

        key = request.headers.get("X-Internal-Key")
        if key != settings.INTERNAL_KEY:
            caller = request.headers.get("X-Larder-Caller", "?")
            if caller not in _warned:
                _warned.add(caller)
                log.warning("internal route %s called without a valid key by %s", request.path, caller)
        return fn(*a, **kw)

    return wrapper
