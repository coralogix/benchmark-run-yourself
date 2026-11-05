"""Route normalisation into a bounded set of templates (shared by mesh + loadgen).

`normalise(service, method, path)` returns, e.g., "GET /api/products/:sku".

* edge-api (public API): a fixed template table; unknown paths become "<METHOD> other".
* every other service (internal, intra- and cross-repo APIs): generic. Each path
  segment is kept if it looks like a literal word (lower-case letters, `-`, `_`,
  or a version tag like `v1`); anything that looks like an id (order refs `LD-...`,
  SKUs, uuids, numbers, user ids `u-0001`, slot/hold/cart ids, hex, anything with a
  digit or upper-case letter) becomes `:id`. Paths with more than 8 segments or
  longer than 256 chars become "<METHOD> other".
* service "any" (loadgen): edge table first, then generic for `/internal/...`.

Unknown methods become "OTHER".
"""

from __future__ import annotations

import re
from functools import lru_cache

METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
EDGE_SERVICES = frozenset({"edge-api", "storefront"})
MAX_SEGMENTS = 8
MAX_PATH = 256

# Literal segments must be listed before parameterised siblings of the same length.
EDGE_TEMPLATES: tuple[str, ...] = (
    "/healthz",
    "/api/products",
    "/api/products/:sku",
    "/api/search",
    "/api/carts",
    "/api/carts/:id",
    "/api/carts/:id/items",
    "/api/carts/:id/items/:sku",
    "/api/carts/:id/coupon",
    "/api/delivery/slots",
    "/api/delivery/slots/next",
    "/api/delivery/holds",
    "/api/delivery/holds/:id",
    "/api/checkout",
    "/api/orders",
    "/api/orders/:ref",
    "/api/orders/:ref/cancel",
    "/api/orders/:ref/receipt",
    "/api/account/:user_id",
    "/api/account/:user_id/orders",
)

# word-like literal: lower-case letters with - or _ separators, or a version tag (v1, v2)
_LITERAL = re.compile(r"^(?:[a-z]+(?:[-_.][a-z]+)*|v\d{1,2})$")
# explicit id shapes (checked first; some are all-letters and would otherwise pass as literals)
_ID = re.compile(
    r"""^(?:
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}   # uuid
      | [0-9a-f]{12,}                                                   # long hex (cart ids, tokens)
      | (?:ld|sku|u|c|h|ch|rf|msg|evt)[-_][a-z]+                         # short-prefixed all-letter ids
    )$""",
    re.X | re.I,
)
_LITERAL_MAX = 40


def _is_literal(seg: str) -> bool:
    return len(seg) <= _LITERAL_MAX and not _ID.match(seg) and bool(_LITERAL.match(seg))


def _compile(templates: tuple[str, ...]):
    by_len: dict[int, list[tuple[tuple[str, ...], str]]] = {}
    for t in templates:
        segs = tuple(s for s in t.split("/") if s)
        by_len.setdefault(len(segs), []).append((segs, t))
    # literal-first ordering: patterns with fewer params win
    for lst in by_len.values():
        lst.sort(key=lambda p: sum(s.startswith(":") for s in p[0]))
    return by_len


_EDGE = _compile(EDGE_TEMPLATES)


def _segments(path: str) -> list[str]:
    return [s for s in path.split("?", 1)[0].split("/") if s]


def _edge_template(segs: list[str]) -> str | None:
    for pat, tmpl in _EDGE.get(len(segs), ()):
        for p, s in zip(pat, segs):
            if p[0] != ":" and p != s:
                break
        else:
            return tmpl
    return None


def _generic_template(segs: list[str]) -> str | None:
    if len(segs) > MAX_SEGMENTS:
        return None
    return "/" + "/".join(s if _is_literal(s) else ":id" for s in segs)


@lru_cache(maxsize=8192)
def _template(service: str, path: str) -> str | None:
    segs = _segments(path)
    if service in EDGE_SERVICES:
        # Known public routes keep their readable parameter names; anything new an engineer adds
        # (feature endpoints) is still labelled, with id-like segments collapsed.
        if segs and segs[0] == "api":
            return _edge_template(segs) or _generic_template(segs)
        return _edge_template(segs)
    if service == "any":
        t = _edge_template(segs)
        if t or not segs or segs[0] != "internal":
            return t
    return _generic_template(segs)


def normalise(service: str, method: str, path: str) -> str:
    m = method.upper()
    if m not in METHODS:
        m = "OTHER"
    # bound cache-key cardinality: very long paths are never a known template
    t = _template(service, path) if len(path) <= MAX_PATH else None
    return f"{m} {t}" if t else f"{m} other"
