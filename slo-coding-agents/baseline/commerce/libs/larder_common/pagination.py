"""
Pagination helpers. page is 1-based everywhere except legacy /v1 (offset).

NB: clamp_page() clamps per_page to 100; catalog's own limit comes from
settings.CATALOG_MAX_PER_PAGE (also 100, from the ini). If one changes...
"""
from larder_common.utils import parse_int

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def clamp_page(page, per_page, default_per_page=DEFAULT_PER_PAGE, max_per_page=MAX_PER_PAGE):
    page = parse_int(page, 1, lo=1)
    per_page = parse_int(per_page, default_per_page, lo=1, hi=max_per_page)
    return page, per_page


def offset_for(page, per_page):
    return (page - 1) * per_page


def page_of(items, page, per_page):
    start = offset_for(page, per_page)
    return items[start:start + per_page]


def envelope(items, page, per_page, total, key="items"):
    return {key: items, "page": page, "per_page": per_page, "total": total}


def from_offset(offset, limit):
    """v1 offset/limit -> page/per_page. Only exact when offset % limit == 0."""
    limit = max(1, int(limit))
    return int(offset) // limit + 1, limit
