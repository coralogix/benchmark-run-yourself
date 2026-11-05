from dataclasses import asdict

from harness.workload.loadgen import (
    build_schedule,
    load_data,
    load_scenario,
    low_stock_probe,
    multibuy_probe,
    order_history_probe,
    search_filter_probe,
    substitutions_probe,
)


def _sched(seed, ts=1.0):
    phases, plans = build_schedule(load_scenario(), seed, 15, 60, ts)
    return phases, [asdict(p) for p in plans]


def test_same_seed_same_schedule():
    a = _sched(1337)
    b = _sched(1337)
    assert a == b
    assert len(a[1]) > 3000


def test_different_seed_differs():
    assert _sched(1337)[1] != _sched(1338)[1]


def test_phases_and_rates():
    phases, plans = _sched(1337)
    assert [p.name for p in phases] == ["warmup", "run-steady", "promo", "psp-degraded", "evening"]
    assert phases[0].end_min == 15 and phases[-1].end_min == 75
    promo = [p for p in plans if p["phase"] == "promo"]
    steady = [p for p in plans if p["phase"] == "run-steady"]
    # 80/min vs 40/min over 10 minutes each
    assert 650 < len(promo) < 950 and 300 < len(steady) < 500
    assert sum(1 for p in promo if p["coupon"]) / len(promo) > 0.35


def test_time_scale_compresses():
    phases, plans = _sched(1337, ts=0.05)
    assert max(p["arrival_s"] for p in plans) <= 75 * 60 * 0.05
    assert 120 < len(plans) < 280  # ~40-80 sessions per wall minute over 3.75 wall minutes


def test_probes():
    mb = {"SKU-1"}
    cart = {"items": [{"sku": "SKU-1", "qty": 3, "unit_price_pence": 100, "line_total_pence": 300}], "discount_pence": 0}
    assert multibuy_probe(cart, mb) is False
    cart["items"][0]["line_total_pence"] = 200
    assert multibuy_probe(cart, mb) is True
    assert multibuy_probe({"items": [{"sku": "SKU-2", "qty": 3}]}, mb) is None
    res = [{"price_pence": 100, "name": "a", "diet": ["vegan"]}, {"price_pence": 200, "name": "b", "diet": ["vegan"]}]
    assert search_filter_probe(res, {"sort": "price_asc", "diet": "vegan"}) is True
    assert search_filter_probe(res, {"sort": "price_desc"}) is False
    assert search_filter_probe(res, {"max_price_pence": 150}) is False


def test_v2_workload_fields():
    assert len(load_data()["users"]) == 1515
    _, plans = _sched(1337)
    co = [p for p in plans if p["checkout"]]
    rate = sum(p["substitutions"] for p in co) / len(co)
    assert 0.25 < rate < 0.35
    assert 0.15 < sum(p["order_history_probe"] for p in plans) / len(plans) < 0.35
    assert len({p["user_id"] for p in plans}) > 450
    scn = load_scenario()
    assert scn["faults"]["evening"]["email"] == {"latency_p50_ms": 2500, "latency_p99_ms": 8000}
    assert scn["faults"]["normal"]["email"] == {"preset": "normal"}


def test_v2_probes():
    assert low_stock_probe({"sku": "x", "only_n_left": 3}) is True
    assert low_stock_probe({"sku": "x", "low_stock": False}) is True
    assert low_stock_probe({"sku": "x"}) is False
    assert low_stock_probe(None) is None
    assert order_history_probe({"orders": [], "page": 1}) is True
    assert order_history_probe({"orders_count": 3}) is False
    assert substitutions_probe({"order_ref": "LD-1", "substitutions": []}) is True
    assert substitutions_probe({"order_ref": "LD-1"}) is False
    assert substitutions_probe(None) is None
