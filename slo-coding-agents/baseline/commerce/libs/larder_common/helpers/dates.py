import datetime

import arrow


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_date(s):
    """Lenient date parse via arrow. Returns None on failure."""
    if not s:
        return None
    try:
        return arrow.get(s).datetime
    except Exception:
        return None


def humanize(dt):
    return arrow.get(dt).humanize()


def days_between(a, b):
    return abs((b - a).days)


def end_of_day(d):
    return datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=datetime.timezone.utc)


def iso_now():
    return utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s):
    """ISO-8601 -> aware datetime, or None. (stdlib only, no arrow)"""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt
