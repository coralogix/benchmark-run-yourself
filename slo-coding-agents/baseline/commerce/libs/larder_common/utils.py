"""
Misc utilities. Please don't add to this file, use larder_common/helpers/.
(everyone has been saying that since 2018)
"""
import datetime
import functools
import hashlib
import re
import threading
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal

from larder_common.log import get_logger

log = get_logger("utils")

# global memo store for @memoize. never evicted (it's only used for small lookups)
_MEMO = {}
_MEMO_LOCK = threading.Lock()


def money_fmt(amount):
    """Format pounds for display: 3.5 -> '£3.50'. Rounds half-up."""
    if amount is None:
        return ""
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "£{:,}".format(d)


def pounds_to_pence(amount):
    if amount is None:
        return None
    return int(round(float(amount) * 100))


def pence_to_pounds(pence):
    if pence is None:
        return None
    return pence / 100.0


def gen_ref(prefix="LD-", n=10):
    return prefix + uuid.uuid4().hex[:n].upper()


def gen_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


def now_iso():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def parse_int(val, default=None, lo=None, hi=None):
    try:
        out = int(val)
    except (TypeError, ValueError):
        return default
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    return out


def to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "y", "t")


def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-")
    return s.lower()


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def deep_get(d, path, default=None):
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def memoize(fn):
    """Memoize on args. NB: global, never cleared."""

    @functools.wraps(fn)
    def wrapper(*args):
        key = (fn.__module__, fn.__name__) + args
        with _MEMO_LOCK:
            if key in _MEMO:
                return _MEMO[key]
        val = fn(*args)
        with _MEMO_LOCK:
            _MEMO[key] = val
        return val

    return wrapper


def retry(times=3, delay=1.0, exceptions=(Exception,)):
    """Old retry decorator. The fulfilment client has its own (clients/retry.py)."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            last = None
            for i in range(times):
                try:
                    return fn(*a, **kw)
                except exceptions as e:  # noqa
                    last = e
                    time.sleep(delay * (i + 1))
            raise last

        return wrapper

    return deco


def timed(label):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            t0 = time.time()
            try:
                return fn(*a, **kw)
            finally:
                took = (time.time() - t0) * 1000
                if took > 500:
                    log.info("%s took %dms", label, took)

        return wrapper

    return deco


def normalise_postcode(pc):
    # upper + single space. see helpers.strings.clean_postcode for the other one
    if not pc:
        return ""
    pc = re.sub(r"\s+", " ", pc.strip().upper())
    return pc


def is_email(s):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", s or "", re.I))


def percent(part, whole):
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def first(iterable, default=None):
    for x in iterable:
        return x
    return default


def flatten(list_of_lists):
    return [x for sub in list_of_lists for x in sub]


def dedupe(seq):
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def round_half_up(amount, places=2):
    """Decimal half-up rounding. The *correct* one (TH 2022). Used by nothing in pricing."""
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(amount)).quantize(q, rounding=ROUND_HALF_UP))


def clamp(n, lo, hi):
    return max(lo, min(hi, n))


def utc_ts():
    return time.time()


def safe_int(v, default=0):
    # like parse_int but never None. (two of these, yes)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def pick(d, *keys):
    return {k: d[k] for k in keys if k in d}


def omit(d, *keys):
    return {k: v for k, v in d.items() if k not in keys}


def env_flag(name, default=False):
    import os

    v = os.environ.get(name)
    if v is None:
        return default
    return to_bool(v)
