"""Helpers for the FEAT-051..FEAT-100 acceptance tests (evaluator-only).

Black-box only: HTTP against the edge, mesh ports, simulator admin APIs and the
supervisor control API. The one grey-box helper (`age_cart_in_db`, FEAT-076) is
documented where it is used.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx

from conftest import OFFSET, mesh_url, wait_until  # noqa: F401  (mesh_url re-exported)


_POOL: list[dict] = []
_POOL_N = [0]


def pooled_user(users, size: int = 4) -> dict:
    """A customer for tests where who orders doesn't matter. Round-robin over a few seeded users so
    the suite doesn't exhaust users.fresh() and no single customer trips the fraud velocity rules."""
    if len(_POOL) < size:
        _POOL.append(users.fresh())
        return _POOL[-1]
    _POOL_N[0] += 1
    return _POOL[_POOL_N[0] % size]


def uid(prefix="acc") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def rhu(x) -> int:
    """round-half-up to an integer, exact (Decimal)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def is_iso_ts(v) -> bool:
    if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", v):
        return False
    try:
        dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def body_of(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return None


def error_of(r: httpx.Response):
    b = body_of(r)
    return b.get("error") if isinstance(b, dict) else None


def find_list(body, *keys):
    """The list in a response whose top-level key isn't pinned by the criterion."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in keys:
            if isinstance(body.get(k), list):
                return body[k]
        lists = [v for v in body.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return None


# ------------------------------------------------------------------ catalogue

def pick_skus(catalog, n, *, min_price=0, max_price=None, min_stock=200, not_tag="3for2", category=None,
              expensive_first=False) -> list[dict]:
    """Like catalog.pick() but can prefer expensive products. Marks them used."""
    prods = list(catalog.products)
    if expensive_first:
        prods.sort(key=lambda p: -p["price_pence"])
    out = []
    for p in prods:
        if p["sku"] in catalog._used:
            continue
        if not_tag and not_tag in p["tags"]:
            continue
        if category and p["category"] != category:
            continue
        if p["price_pence"] < min_price or (max_price is not None and p["price_pence"] > max_price):
            continue
        if catalog.stock.get(p["sku"], 0) < min_stock:
            continue
        out.append(p)
        if len(out) == n:
            break
    assert len(out) == n, "not enough matching seed products"
    catalog._used.update(p["sku"] for p in out)
    return out


def qty_for(product: dict, goods_pence: int = 1500, cap: int = 24) -> int:
    """Smallest qty (<= cap) whose line total reaches goods_pence (orders stay above any minimum order value)."""
    q = -(-goods_pence // product["price_pence"])
    return max(1, min(cap, q))


# ------------------------------------------------------------------ orders

class Placed(dict):
    """Checkout 201 body + cart_id, cart (as priced right before checkout), lines actually ordered."""


def place_order(shop, user: dict, lines: list[tuple[str, int]], coupon: str | None = None, tries: int = 4,
                **extra) -> Placed:
    """Place an order through the public API and assert it is confirmed.

    On 409 price_mismatch (the seeded float-pricing defect, unrelated to the features
    tested here) the first line's quantity is bumped and the order retried.
    """
    lines = list(lines)
    last = None
    for _ in range(tries):
        cid = shop.cart(user["user_id"])
        for sku, qty in lines:
            r = shop.add(cid, sku, qty)
            assert r.status_code == 200, f"add {sku}x{qty}: {r.status_code} {r.text}"
        if coupon:
            r = shop.edge.post(f"/api/carts/{cid}/coupon", json={"code": coupon})
            assert r.status_code == 200, r.text
        cart = shop.edge.get(f"/api/carts/{cid}").json()
        hid = shop.hold(cid, user["postcode"])
        r = shop.checkout(cid, hid, **extra)
        last = r
        if r.status_code == 409 and error_of(r) == "price_mismatch":
            sku, qty = lines[0]
            lines[0] = (sku, qty + 1)
            continue
        assert r.status_code == 201, f"checkout: {r.status_code} {r.text}"
        out = Placed(r.json())
        out.update(cart_id=cid, cart=cart, lines=lines, hold_id=hid)
        return out
    raise AssertionError(f"checkout kept failing: {last.status_code} {last.text}")


def order_internal(mesh, ref: str) -> dict:
    r = mesh("orders").get(f"/internal/orders/{ref}")
    assert r.status_code == 200, r.text
    return r.json()


def dispatch(mesh, ref: str) -> httpx.Response:
    return mesh("warehouse").post(f"/internal/dispatch/{ref}", json={})


def cancel_order(edge, ref: str) -> httpx.Response:
    return edge.post(f"/api/orders/{ref}/cancel", json={})


def require_cancel(edge, ref: str) -> dict:
    """Cancel via FEAT-002. If cancellation doesn't exist the dependent feature can't be complete."""
    r = cancel_order(edge, ref)
    assert r.status_code == 200, (
        f"POST /api/orders/{ref}/cancel -> {r.status_code} {r.text[:200]}: this feature depends on "
        "FEAT-002 cancellation, which is missing or broken")
    return r.json()


# ------------------------------------------------------------------ simulators

def emails_for(email_admin, ref: str, template: str | None = None) -> list[dict]:
    params = {"limit": 50000}
    if template:
        params["template"] = template
    msgs = email_admin.get("/admin/messages", params=params)["messages"]
    return [m for m in msgs if isinstance(m.get("data"), dict) and str(m["data"].get("order_ref")) == ref]


def emails_to(email_admin, to: str) -> list[dict]:
    return email_admin.get("/admin/messages", params={"to": to, "limit": 50000})["messages"]


def partner_events(partner_admin, ref: str, event: str | None = None) -> list[dict]:
    ds = partner_admin.get("/admin/deliveries", params={"limit": 500})["deliveries"]
    return [d for d in ds if str(d.get("order_ref")) == ref and (event is None or d.get("event") == event)]


def psp_ledger(psp_admin) -> dict:
    return psp_admin.get("/admin/ledger")


def bus_idle(mesh) -> bool:
    try:
        r = mesh("bus").get("/internal/bus/topics")
        topics = r.json().get("topics", [])
        return r.status_code == 200 and all((t.get("depth") or 0) == 0 for t in topics)
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def settle(mesh, extra_s: float = 3.0, timeout_s: float = 30):
    """Wait for the bus to drain, then a little longer (webhook dispatcher ticks every 2s)."""
    wait_until(lambda: bus_idle(mesh), timeout_s)
    time.sleep(extra_s)


# ------------------------------------------------------------------ supervisor control

def control_client() -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{7070 + OFFSET}", timeout=180)


def wait_healthy(service: str, timeout_s: float = 90) -> bool:
    def ok():
        try:
            return httpx.get(mesh_url(service) + "/healthz", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False
    return bool(wait_until(ok, timeout_s, 0.5))


def stop_service(service: str):
    with control_client() as c:
        c.post(f"/services/{service}/stop")


def start_service(service: str):
    with control_client() as c:
        c.post(f"/services/{service}/start")
    assert wait_healthy(service), f"{service} did not come back"


# ------------------------------------------------------------------ grey-box (FEAT-076 only)

def workspace() -> Path | None:
    ws = os.environ.get("LARDER_EVAL_WORKSPACE")
    return Path(ws) if ws else None


def _shift(value, delta: dt.timedelta):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        secs = delta.total_seconds()
        return value - (secs * 1000 if value > 1e11 else secs)
    s = str(value)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return value
    out = (d - delta).isoformat()
    if s.endswith("Z"):
        out = out.replace("+00:00", "Z")
    if "." not in s and "." in out:
        out = re.sub(r"\.\d+", "", out)
    if "T" not in s:
        out = out.replace("T", " ")
    return out


def age_cart_in_db(db_path: Path, cart_id: str, hours: float) -> int:
    """Move every timestamp-like column (`*_at`, `created`, `updated`) of the rows belonging to
    `cart_id` (any table with a cart_id column) `hours` into the past. Returns rows touched."""
    delta = dt.timedelta(hours=hours)
    con = sqlite3.connect(str(db_path), timeout=30)
    n = 0
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            if "cart_id" not in cols:
                continue
            tcols = [c for c in cols if c.endswith("_at") or c in ("created", "updated", "last_seen",
                                                                   "last_activity", "touched")]
            if not tcols:
                continue
            rows = con.execute(f'SELECT rowid, {", ".join(tcols)} FROM "{t}" WHERE cart_id = ?', (cart_id,)).fetchall()
            for row in rows:
                rowid, vals = row[0], row[1:]
                new = [_shift(v, delta) if c != "hold_expires_at" else v for c, v in zip(tcols, vals)]
                con.execute(f'UPDATE "{t}" SET {", ".join(f"{c} = ?" for c in tcols)} WHERE rowid = ?',
                            (*new, rowid))
                n += 1
        con.commit()
    finally:
        con.close()
    return n
