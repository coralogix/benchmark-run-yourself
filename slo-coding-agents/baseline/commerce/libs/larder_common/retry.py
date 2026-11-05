"""
Retry decorator for outbound calls (JW 2018).

    @retrying(extra_attempts=2, wait=0.5)
    def call(): ...

Retries on any exception: the network between us and fulfilment used to
drop connections a lot (the old VPN), and a retry nearly always worked.
Moved here from larder/clients/retry.py in the 2023 split; every service's
outbound calls go through it via larder_common.http.
"""
import functools
import time

from larder_common.log import get_logger

log = get_logger("http.retry")

DEFAULT_EXTRA_ATTEMPTS = 2
DEFAULT_WAIT = 0.5


def retrying(extra_attempts=DEFAULT_EXTRA_ATTEMPTS, wait=DEFAULT_WAIT, label=None):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt >= extra_attempts:
                        raise
                    attempt += 1
                    log.warning("%s failed (%s: %s), retry %d/%d", label or fn.__name__, type(e).__name__, e,
                                attempt, extra_attempts)
                    time.sleep(wait)

        return wrapper

    return deco


def call_with_retry(fn, *args, extra_attempts=DEFAULT_EXTRA_ATTEMPTS, wait=DEFAULT_WAIT, label=None, **kwargs):
    """Non-decorator form, so the attempt count can come from settings at call time."""
    return retrying(extra_attempts=extra_attempts, wait=wait, label=label)(fn)(*args, **kwargs)
