"""Offline oracles for the acceptance suite (evaluator-only helper, not a test module).

* canonical(): the integer-pence order total the tax service computes.
* v1_total() / v2_total(): reproductions of the commerce pricing engines' float
  arithmetic (legacy/pricing_v1.py and services/pricing/engine_v2.py at baseline), used
  only to *select* carts on which the float engines disagree with the canonical rule
  (SF-1) and carts on which they all agree (SMOKE, so smoke never depends on SF-1).

Prices come from the seed files (catalog price_pence == inventory unit_price_pence).
"""
from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
import uuid

import httpx


def canonical(lines: list[tuple[int, int]], percent_off: float = 0, free_delivery: bool = False) -> dict:
    """Totals {subtotal_pence, discount_pence, delivery_fee_pence, total_pence} for lines [(unit_price_pence, qty)]."""
    sub = sum(int(p) * int(q) for p, q in lines)
    pct = float(percent_off or 0)
    if pct <= 0:
        disc = 0
    elif pct == int(pct):
        disc = (2 * sub * int(pct) + 100) // 200  # round half up of sub*pct/100
    else:
        disc = int(math.floor(sub * pct / 100 + 0.5))
    delivery = 0 if free_delivery or (sub - disc) >= 4000 else 399
    return {"subtotal_pence": sub, "discount_pence": disc, "delivery_fee_pence": delivery,
            "total_pence": sub - disc + delivery}


def _pounds(lines):
    return [(p / 100.0, q) for p, q in lines]


def v1_total(lines: list[tuple[int, int]], percent_off: float = 0, free_delivery: bool = False) -> int:
    """legacy/pricing_v1.price_cart total_pence (float pounds, truncating to_pence)."""
    sub = 0.0
    for up, q in _pounds(lines):
        sub += up * q
    disc = sub * percent_off / 100 if percent_off else 0.0
    if free_delivery:
        d = 0.0
    elif sub - disc < 40.0:
        d = 3.99
    else:
        d = 0.0
    total = sub - disc + d
    if disc:
        return int(total * 100)
    if d:
        return int(round(total, 2) * 100 + 0.5)
    return int(round(total, 2) * 100)


def v2_total(lines: list[tuple[int, int]], percent_off: float = 0, free_delivery: bool = False) -> int:
    """services/pricing/engine_v2.price_cart total_pence (float pounds, round())."""
    sub = 0.0
    for up, q in _pounds(lines):
        sub += round(up * q, 2)
    sub = round(sub, 2)
    disc = round(sub * percent_off / 100.0, 2) if percent_off else 0.0
    after = sub - disc
    d = 0.0 if free_delivery else (3.99 if after < 40.0 else 0.0)
    return int(round(round(after + d, 2) * 100))


def engines_agree(lines, percent_off=0, free_delivery=False) -> bool:
    c = canonical(lines, percent_off, free_delivery)["total_pence"]
    return v1_total(lines, percent_off, free_delivery) == c and v2_total(lines, percent_off, free_delivery) == c


# ------------------------------------------------------------------ supervisor / processes

def _supervisor_url() -> str:
    from conftest import OFFSET
    return f"http://127.0.0.1:{7070 + OFFSET}"


def service_pid(service: str) -> int | None:
    st = httpx.get(_supervisor_url() + "/status", timeout=10).json()
    return st["services"][service]["pid"]


def _darwin_footprint(pid: int) -> int | None:
    """macOS phys_footprint (what Activity Monitor calls Memory). Unlike RSS it includes
    compressed pages, so it does not shrink when the OS compresses an idle heap."""
    import ctypes
    import ctypes.util
    import struct

    lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(512)
    if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(2), buf) != 0:  # RUSAGE_INFO_V2
        return None
    return struct.unpack_from("<Q", buf.raw, 16 + 7 * 8)[0]  # ri_phys_footprint


def service_mem_bytes(service: str) -> int | None:
    """Memory of the service's process tree (pid from the supervisor's /status): phys_footprint on
    macOS, RSS elsewhere; falls back to the supervisor's resources.jsonl RSS samples."""
    pid = service_pid(service)
    try:
        import psutil

        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
        if sys.platform == "darwin":
            vals = [_darwin_footprint(p.pid) for p in procs]
            if all(v is not None for v in vals):
                return sum(vals)
        return sum(p.memory_info().rss for p in procs)
    except Exception:  # noqa: BLE001
        from conftest import ARTIFACTS

        path = ARTIFACTS / "resources.jsonl"
        last = None
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("service") == service:
                    last = rec.get("rss_bytes")
        return last


def signal_service(service: str, sig: int) -> None:
    pid = service_pid(service)
    pids = [pid]
    try:
        import psutil

        pids += [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except Exception:  # noqa: BLE001
        pass
    for p in pids:
        try:
            os.kill(p, sig)
        except ProcessLookupError:
            pass


def freeze(service: str) -> None:
    signal_service(service, signal.SIGSTOP)


def thaw(service: str) -> None:
    signal_service(service, signal.SIGCONT)


def wait_healthy(url: str, timeout_s: float = 60) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            if httpx.get(url + "/healthz", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


# ------------------------------------------------------------------ direct orders (section 4 contract)

def direct_order_body(lines: list[tuple[str, int, int]], *, user_id: str, email: str | None = None,
                      token: str = "tok_ok", order_ref: str | None = None) -> dict:
    """POST /internal/orders body with canonical totals for lines [(sku, qty, unit_price_pence)]."""
    t = canonical([(pp, q) for _, q, pp in lines])
    return {
        "order_ref": order_ref or f"ACC-{uuid.uuid4().hex[:12].upper()}",
        "user_id": user_id,
        "email": email or f"{user_id}.{uuid.uuid4().hex[:6]}@example.test",
        "items": [{"sku": s, "qty": q, "unit_price_pence": pp} for s, q, pp in lines],
        "coupon": None,
        **t,
        "hold_id": None,
        "payment_token": token,
    }
