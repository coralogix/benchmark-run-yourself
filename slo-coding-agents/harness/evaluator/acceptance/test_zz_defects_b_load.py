"""Seeded defects with latency / resource outcomes under load: SF-3, SF-5, SF-7, FF-6.

Thresholds were calibrated on the baseline (defect present) and on the reference fixes
(harness/evaluator/reference/*.patch) with a wide margin either side; see the per-test notes.
"""
from __future__ import annotations

import random
import statistics
import threading
import time
import uuid

import httpx
import pytest

from conftest import HC_SKUS, edge_url, mesh_url
from larder_support import service_mem_bytes

WORDS = ["milk", "bread", "apple", "cheese", "pasta", "rice", "tea", "coffee", "oil", "butter", "yogurt", "chicken",
         "beans", "soup", "juice", "eggs", "flour", "sugar", "jam", "honey", "oat", "banana", "tomato", "crisps"]


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


def _timed(client, method, url, **kw):
    t = time.perf_counter()
    try:
        r = client.request(method, url, **kw)
        code = r.status_code
    except httpx.HTTPError:
        code = 0
    return time.perf_counter() - t, code


# ------------------------------------------------------------------ SF-3

@pytest.mark.item("SF-3")
def test_sf3_unique_searches_do_not_stall_search_or_product_pages(catalog):
    """~450 never-seen-before searches (enough to cross the baseline cache's 400-entry safety
    valve at least once) while two clients keep viewing product pages.

    Baseline: the valve flushes the cache and rebuilds the index under the global lock
    (>= 3 s: 299 products x 10 ms throttle, plus queries), stalling every in-flight search
    and every product page (recommendations searches the category). Fixed: bounded cache,
    no stop-the-world rebuild. Thresholds (2.5 s) sit below the minimum stall and well above
    the fixed system's worst case under a loaded box (search max ~1.4 s, detail max ~1.6 s).
    """
    n, searchers = 450, 2
    tag = uuid.uuid4().hex[:6]
    rnd = random.Random(tag)
    queries = [f"{rnd.choice(WORDS)} {rnd.choice(WORDS)} {tag}{i}" for i in range(n)]
    skus = [p["sku"] for p in catalog.products[::28] if p["sku"] not in HC_SKUS]
    search_lat, detail_lat, bad_codes = [], [], []
    stop = threading.Event()

    def searcher(chunk):
        with httpx.Client(base_url=edge_url(), timeout=60) as c:
            for q in chunk:
                d, code = _timed(c, "GET", "/api/search", params={"q": q})
                search_lat.append(d)
                if code != 200:
                    bad_codes.append(("search", code))

    def sampler(k):
        i = k
        with httpx.Client(base_url=edge_url(), timeout=60) as c:
            while not stop.is_set():
                d, code = _timed(c, "GET", f"/api/products/{skus[i % len(skus)]}")
                detail_lat.append(d)
                if code != 200:
                    bad_codes.append(("detail", code))
                i += 2
                stop.wait(0.1)

    samplers = [threading.Thread(target=sampler, args=(k,)) for k in range(2)]
    workers = [threading.Thread(target=searcher, args=(queries[k::searchers],)) for k in range(searchers)]
    for t in samplers + workers:
        t.start()
    for t in workers:
        t.join()
    stop.set()
    for t in samplers:
        t.join()

    slow_search = [round(x, 2) for x in search_lat if x > 2.5]
    slow_detail = [round(x, 2) for x in detail_lat if x > 2.5]
    summary = (f"search n={len(search_lat)} p50={_q(search_lat, .5):.3f} max={max(search_lat):.2f}; "
               f"detail n={len(detail_lat)} p50={_q(detail_lat, .5):.3f} max={max(detail_lat):.2f}")
    assert not bad_codes, f"non-200 responses {bad_codes[:5]} ({summary})"
    assert not slow_detail, f"product pages over 2.5s while searching: {slow_detail} ({summary})"
    assert len(slow_search) <= 1, f"searches over 2.5s: {slow_search} ({summary})"


# ------------------------------------------------------------------ SF-5

@pytest.mark.item("SF-5")
def test_sf5_product_pages_stay_fast_under_concurrent_browsing(catalog):
    """16 concurrent shoppers viewing product pages (a popular set of 30), plus one shopper
    paging the product list. Baseline: every view costs recommendations 13 sequential catalog
    calls + a search; recommendations/catalog saturate and all browsing queues behind it.
    """
    skus = [p["sku"] for p in catalog.products[3::38] if p["sku"] not in HC_SKUS][:30]
    with httpx.Client(base_url=edge_url(), timeout=60) as c:
        for s in skus:  # warm-up pass (caches, connections)
            c.get(f"/api/products/{s}")
    detail, listing, bad = [], [], []
    stop = threading.Event()

    def shopper(k):
        i = k
        with httpx.Client(base_url=edge_url(), timeout=60) as c:
            while not stop.is_set():
                d, code = _timed(c, "GET", f"/api/products/{skus[i % len(skus)]}")
                detail.append(d)
                if code != 200:
                    bad.append(code)
                i += 7

    def lister():
        i = 0
        with httpx.Client(base_url=edge_url(), timeout=60) as c:
            while not stop.is_set():
                d, code = _timed(c, "GET", "/api/products", params={"page": 1 + i % 10, "per_page": 20})
                listing.append(d)
                if code != 200:
                    bad.append(code)
                i += 1
                stop.wait(0.2)

    threads = [threading.Thread(target=shopper, args=(k,)) for k in range(16)] + [threading.Thread(target=lister)]
    for t in threads:
        t.start()
    time.sleep(12)
    stop.set()
    for t in threads:
        t.join()
    summary = (f"detail n={len(detail)} p50={_q(detail, .5):.3f} p95={_q(detail, .95):.3f}; "
               f"list n={len(listing)} p50={_q(listing, .5):.3f} p95={_q(listing, .95):.3f}")
    assert not bad, f"non-200 responses {bad[:5]} ({summary})"
    # with the aiohttp mesh: baseline p50 ~0.13 s / p95 ~0.17 s, reference fix p50 ~0.026 s / p95 ~0.04 s
    assert _q(detail, .5) < 0.075 and _q(detail, .95) < 0.15, f"product detail too slow under load ({summary})"
    assert _q(listing, .95) < 0.25, f"product listing too slow under load ({summary})"


# ------------------------------------------------------------------ SF-7

@pytest.mark.item("SF-7")
def test_sf7_abandoned_carts_do_not_grow_cart_memory(catalog):
    """900 carts that are created, filled, looked at 12 times and abandoned.

    Baseline keeps every cart and every priced snapshot of it (one per read) in memory
    forever: ~45 KB per cart here, ~40 MB in total. A fix that evicts abandoned carts and/or
    stops keeping every priced snapshot stays well under 16 MB (reference fix: <1 MB).
    Memory is phys_footprint on macOS (RSS hides compressed pages), RSS elsewhere.
    """
    skus = [p["sku"] for p in catalog.products if catalog.stock.get(p["sku"], 0) >= 200][:120]
    n, workers, reads = 900, 8, 12
    limit_mb = 16.0

    def make(rng, c, i):
        cid = c.post("/api/carts", json={"user_id": f"acc-sf7-{i}"}).json()["cart_id"]
        for j in range(2):
            c.post(f"/api/carts/{cid}/items", json={"sku": skus[(i * 7 + j * 13) % len(skus)], "qty": 1 + j})
        for _ in range(reads):
            c.get(f"/api/carts/{cid}")

    def run(lo, hi):
        def w(k):
            with httpx.Client(base_url=edge_url(), timeout=60) as c:
                for i in range(lo + k, hi, workers):
                    make(None, c, i)
        ts = [threading.Thread(target=w, args=(k,)) for k in range(workers)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    run(0, 150)  # warm-up: allocator arenas, connection pools
    time.sleep(1)
    before = service_mem_bytes("cart")
    assert before, "cannot read the cart service's memory"
    growth = 0.0
    done = 150
    for step_end in range(450, 150 + n + 1, 300):
        run(done, step_end)
        done = step_end
        growth = (service_mem_bytes("cart") - before) / 1e6
        if growth > limit_mb * 1.5:
            break  # clearly unbounded; no need to keep loading the box
    time.sleep(2)
    growth = (service_mem_bytes("cart") - before) / 1e6
    assert growth < limit_mb, (f"cart memory grew {growth:.1f} MB over {done - 150} abandoned carts "
                               f"(limit {limit_mb} MB, {growth * 1000 / max(1, done - 150):.1f} KB/cart)")


# ------------------------------------------------------------------ FF-6

@pytest.mark.item("FF-6")
def test_ff6_fraud_scoring_latency_does_not_grow_with_history():
    """1,500 fraud score calls straight to fraud's mesh port. Baseline re-reads, re-parses and
    rewrites the whole history file and scans it linearly on every call, so latency grows with
    the number of payments ever scored. Fails fast once a window is clearly degraded.
    """
    url = mesh_url("fraud") + "/internal/fraud/score"
    tag = uuid.uuid4().hex[:6]
    windows = []
    lat = []
    with httpx.Client(timeout=30) as c:
        for i in range(1500):
            body = {"order_ref": f"ACC-FF6-{tag}-{i}", "email": f"ff6-{tag}-{i}@example.test",
                    "user_id": f"acc-ff6-{i}", "amount_pence": 2500 + i % 50, "token": "tok_ok"}
            t = time.perf_counter()
            r = c.post(url, json=body)
            lat.append(time.perf_counter() - t)
            assert r.status_code == 200 and r.json().get("decision"), r.text[:200]
            if len(lat) == 100:
                windows.append(statistics.median(lat))
                lat = []
                first = windows[0]
                if windows[-1] > max(0.025, 3 * first):
                    break
    first, last = windows[0], windows[-1]
    trail = ", ".join(f"{w * 1000:.1f}" for w in windows)
    assert last <= max(0.025, 3 * first), (f"fraud score p50 per 100 calls (ms): {trail}; grew from "
                                            f"{first * 1000:.1f} to {last * 1000:.1f} ms after {len(windows) * 100} calls")
