"""
Date parsing for the 2016 promo spreadsheet import (MK).

The spreadsheet export wrote dates as 31/Dec/2016 (or 31-Dec-2016), and
"none" / "n/a" for codes that don't expire.
"""
import datetime
import re

from larder_common.log import legacy_log

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_NO_EXPIRY = ("none", "n/a", "-", "")

_SPLIT = re.compile(r"[/\-\s]+")


def parse_promo_date(raw):
    s = (raw or "").strip().lower()
    if s in _NO_EXPIRY:
        return None
    day, mon, year = _SPLIT.split(s)
    month = _MONTHS[mon[:3]]
    if len(year) == 2:
        year = "20" + year
    legacy_log("debug", "legacy promo date %s", raw)
    return datetime.datetime(int(year), month, int(day), 23, 59, 59, tzinfo=datetime.timezone.utc)


def format_promo_date(dt):
    return dt.strftime("%d/%b/%Y")
