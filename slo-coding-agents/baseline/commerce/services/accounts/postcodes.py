"""
Postcode helpers for accounts.

Yes, there are already two of these (utils.normalise_postcode and
helpers.strings.clean_postcode). This one was written in 2022 for the
addresses project because "neither of the others does the outward code".
It also disagrees with both about lower-case input with no space.
"""
import re

_FULL = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$")


def normalise(pc):
    """'ld11aa' -> 'LD1 1AA'. Returns the input stripped if it doesn't parse."""
    s = (pc or "").strip().upper()
    m = _FULL.match(s)
    if not m:
        return s
    return "%s %s" % (m.group(1), m.group(2))


def outward(pc):
    """'LD1 1AA' -> 'LD1'."""
    s = normalise(pc)
    return s.split(" ")[0] if " " in s else s[:-3]


def same_area(a, b):
    return outward(a) == outward(b)
