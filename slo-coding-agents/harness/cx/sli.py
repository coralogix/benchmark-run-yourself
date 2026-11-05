"""Service level indicators shared by Coralogix SLO creation and local scoring.

One definition list drives both:
  * the Coralogix SLOs deployed to the SLO-guided account, and
  * the SLIs the evaluator computes from the harness ingress log for EVERY run,
    so both conditions are measured against identical objectives even though
    only one of them can see the SLOs while working.

All SLIs are measured at the harness-owned ingress proxy (mesh), so an agent
cannot change the measurement by editing application code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

INGRESS_COUNTER = "larder_ingress_requests_total"
INGRESS_HIST = "larder_ingress_request_duration_seconds"
ENTITY_COUNTER = "larder_ingress_entity_requests_total"
MIN_ENTITY_EVENTS = 5  # worst-entity signals ignore entities with fewer requests in the window
ENTITY_ELIGIBILITY_WINDOW = "60m"  # ...and entities with fewer than SLI.min_entity_events requests over this window


@dataclass(frozen=True)
class SLI:
    key: str
    name: str
    service: str  # mesh `service` label (callee service name)
    kind: str  # availability | latency | window
    target: float  # percent
    criticality: str  # critical | standard
    description: str
    routes: tuple[str, ...] = ()  # normalised mesh routes; empty = every route
    bad_reasons: tuple[str, ...] = ()  # availability: 4xx error reasons that count as bad
    threshold_s: float | None = None  # latency: le bucket boundary
    labels: dict = field(default_factory=dict)
    group_by: str | None = None  # entity type (user | sku | postcode): one SLO permutation per entity
    min_entity_events: int = MIN_ENTITY_EVENTS  # grouped: requests over the last 60 min for an entity to count
    # window-based SLIs: each minute is good or bad; the SLI is the share of good minutes
    window_stat: str | None = None  # p95 (seconds) | error_ratio (5xx share)
    window_threshold: float | None = None  # a minute is good when the statistic is at or below this


SLIS: tuple[SLI, ...] = (
    SLI(
        key="checkout_success",
        name="Larder · Checkout success",
        service="edge-api",
        kind="availability",
        target=99.0,
        criticality="critical",
        routes=("POST /api/checkout",),
        bad_reasons=("price_mismatch",),
        description="Share of checkout requests at the edge that do not fail with a 5xx/timeout or a price_mismatch rejection. Card declines and genuine out-of-stock responses count as good.",
    ),
    SLI(
        key="customer_api_availability",
        name="Larder · Customer API availability",
        service="edge-api",
        kind="availability",
        target=99.5,
        criticality="critical",
        description="Share of all customer requests at the edge that do not return a 5xx (including 502/504 from the edge when the customer API is down or stalled).",
    ),
    SLI(
        key="browse_latency",
        name="Larder · Catalogue browse latency",
        service="edge-api",
        kind="latency",
        target=99.0,
        threshold_s=0.5,
        criticality="standard",
        routes=("GET /api/products", "GET /api/products/:sku"),
        description="Share of product listing and product detail requests served in 500 ms or less.",
    ),
    SLI(
        key="search_latency",
        name="Larder · Search latency",
        service="edge-api",
        kind="latency",
        target=95.0,
        threshold_s=0.75,
        criticality="standard",
        routes=("GET /api/search",),
        description="Share of search requests served in 750 ms or less.",
    ),
    SLI(
        key="delivery_slots_availability",
        name="Larder · Delivery slot availability",
        service="edge-api",
        kind="availability",
        target=99.0,
        criticality="standard",
        routes=("GET /api/delivery/slots", "POST /api/delivery/holds"),
        description="Share of slot listing and slot hold requests that do not return a 5xx.",
    ),
    SLI(
        key="order_confirmation_availability",
        name="Larder · Order confirmation availability",
        service="orders",
        kind="availability",
        target=99.5,
        criticality="critical",
        routes=("POST /internal/orders",),
        description="Share of order confirmation calls into the orders service (stock reservation, slot booking, payment capture) that do not return a 5xx.",
    ),
    SLI(
        key="payments_availability",
        name="Larder · Payments availability",
        service="payments",
        kind="availability",
        target=99.5,
        criticality="critical",
        description="Share of calls into the payments service (charges, and refunds once they exist) that do not return a 5xx.",
    ),
    SLI(
        key="orders_latency",
        name="Larder · Orders API latency",
        service="orders",
        kind="latency",
        target=98.0,
        threshold_s=1.0,
        criticality="standard",
        description="Share of all calls into the orders service served in 1 second or less.",
    ),
)


GROUPED_SLIS: tuple[SLI, ...] = (
    SLI(
        key="product_page_availability_by_sku",
        name="Larder · Product page availability per product",
        service="edge-api",
        kind="availability",
        target=99.0,
        criticality="standard",
        group_by="sku",
        description="Per product (one permutation per SKU, ~1,150): share of product detail page requests that do not return a 5xx.",
    ),
    SLI(
        key="customer_availability_by_customer",
        name="Larder · Customer experience per customer",
        service="edge-api",
        kind="availability",
        target=98.0,
        criticality="critical",
        group_by="user",
        # one stray error in a single short session would read as 10 %: require sustained traffic
        min_entity_events=60,
        description="Per customer (one permutation per customer, ~1,500): share of that customer's requests to the public API that do not return a 5xx.",
    ),
    SLI(
        key="delivery_slots_by_postcode",
        name="Larder · Delivery slot availability per postcode",
        service="edge-api",
        kind="availability",
        target=99.0,
        criticality="standard",
        group_by="postcode",
        description="Per postcode (one permutation per postcode, ~1,000): share of delivery slot listings that do not return a 5xx.",
    ),
)
# Window-based (time-slice) SLOs: the objective is a share of good one-minute windows, not of good requests.
WINDOW_SLIS: tuple[SLI, ...] = (
    SLI(
        key="checkout_latency_minutes",
        name="Larder · Checkout latency minutes",
        service="edge-api",
        kind="window",
        target=85.0,
        criticality="critical",
        routes=("POST /api/checkout",),
        window_stat="p95",
        window_threshold=4.0,
        description="Share of one-minute windows in which the 95th percentile of successful checkouts at the edge took 4 seconds or less. Minutes without checkouts do not count.",
    ),
    SLI(
        key="customer_error_minutes",
        name="Larder · Customer error-free minutes",
        service="edge-api",
        kind="window",
        target=90.0,
        criticality="critical",
        window_stat="error_ratio",
        window_threshold=0.01,
        description="Share of one-minute windows in which 1% or fewer of all customer requests at the edge returned a 5xx.",
    ),
    SLI(
        key="account_page_latency_minutes",
        name="Larder · Account page latency minutes",
        service="edge-api",
        kind="window",
        target=95.0,
        criticality="standard",
        routes=("GET /api/account/:user_id/orders",),
        window_stat="p95",
        window_threshold=1.0,
        description="Share of one-minute windows in which the 95th percentile of order history requests took 1 second or less.",
    ),
)
SLIS = SLIS + GROUPED_SLIS + WINDOW_SLIS

# Every SLI the local evaluator measures. Both conditions are measured from the same ingress records.
MEASURED_SLIS: tuple[SLI, ...] = SLIS


def slo_set(condition: str) -> tuple[SLI, ...]:
    """The SLOs a condition deploys to Coralogix (the evaluator always measures MEASURED_SLIS)."""
    if condition == "basic":
        return ()
    if condition == "slo-guided":
        return SLIS
    raise ValueError(f"unknown condition {condition!r}")


def _ent_sel(sli: SLI, run_id: str, extra: str = "") -> str:
    parts = [f'service="{sli.service}"', f'entity_type="{sli.group_by}"', f'bench_run_id="{run_id}"']
    if sli.routes:
        parts.append('route=~"%s"' % "|".join(sli.routes))
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}"


def _fmt_le(v: float) -> str:
    # Coralogix keeps the OTel bucket boundary as written by the SDK, e.g. "0.5", "0.75", "1".
    return ("%g" % v)


def selector(sli: SLI, run_id: str, extra: str = "") -> str:
    parts = [f'service="{sli.service}"', f'bench_run_id="{run_id}"']
    if sli.routes:
        parts.append('route=~"%s"' % "|".join(sli.routes))
    else:
        parts.append('route!~"GET /healthz|GET /internal/info"')  # probes are not customer traffic
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}"


def good_total_promql(sli: SLI, run_id: str, rng: str = "1m") -> tuple[str, str]:
    """Event-based SLO queries. Coralogix requires a [1m] range for SLOs. Grouped SLIs return one
    series per entity (`sum by (entity)`), which Coralogix turns into one permutation per entity."""
    if sli.group_by:
        not_5xx = 'code_class!="5xx"'
        good = f"sum by (entity) (increase({ENTITY_COUNTER}{_ent_sel(sli, run_id, not_5xx)}[{rng}]))"
        total = f"sum by (entity) (increase({ENTITY_COUNTER}{_ent_sel(sli, run_id)}[{rng}]))"
        return good, total
    if sli.kind == "window":  # counts of good and of all one-minute windows with traffic, over `rng`
        val = window_value_promql(sli, run_id)
        good = f"sum_over_time(({val} <= bool {sli.window_threshold:g})[{rng}:1m])"
        total = f"count_over_time(({val})[{rng}:1m])"
        return good, total
    if sli.kind == "availability":
        extra = 'code_class!="5xx"'
        if sli.bad_reasons:
            extra += ',reason!~"%s"' % "|".join(sli.bad_reasons)
        good = f"sum(increase({INGRESS_COUNTER}{selector(sli, run_id, extra)}[{rng}]))"
        total = f"sum(increase({INGRESS_COUNTER}{selector(sli, run_id)}[{rng}]))"
    else:
        le = 'le="%s"' % _fmt_le(sli.threshold_s)
        good = f"sum(increase({INGRESS_HIST}_bucket{selector(sli, run_id, le)}[{rng}]))"
        total = f"sum(increase({INGRESS_HIST}_count{selector(sli, run_id)}[{rng}]))"
    return good, total


def window_value_promql(sli: SLI, run_id: str) -> str:
    """The per-minute statistic a window-based SLI compares with its threshold."""
    if sli.window_stat == "p95":
        ok = 'code_class="2xx"' if sli.routes == ("POST /api/checkout",) else ""
        return f"histogram_quantile(0.95, sum by (le) (rate({INGRESS_HIST}_bucket{selector(sli, run_id, ok)}[1m])))"
    is_5xx = 'code_class="5xx"'
    bad = f"(sum(rate({INGRESS_COUNTER}{selector(sli, run_id, is_5xx)}[1m])) or vector(0))"
    total = f"sum(rate({INGRESS_COUNTER}{selector(sli, run_id)}[1m]))"
    return f"({bad} / {total})"


def window_bad_ratio_promql(sli: SLI, run_id: str, rng: str) -> str:
    """Share of bad one-minute windows over `rng` (a subquery); 0 when no minute had traffic."""
    return f"(avg_over_time(({window_value_promql(sli, run_id)} > bool {sli.window_threshold:g})[{rng}:1m]) or vector(0))"


def entity_bad_ratio_promql(sli: SLI, run_id: str, rng: str) -> str:
    """Per-entity share of bad events over `rng`, only for entities with enough requests in
    `rng` and at least sli.min_entity_events over the last hour (when that is above the base
    threshold). Counts bad events directly so entities with no good events at all are kept
    (they matter most). Sparse per-route SLIs (one checkout per customer) set a lower
    min_entity_events, so their base eligibility is that same lower number."""
    total = f"sum by (entity) (increase({ENTITY_COUNTER}{_ent_sel(sli, run_id)}[{rng}]))"
    is_5xx = 'code_class="5xx"'
    bad = f"sum by (entity) (increase({ENTITY_COUNTER}{_ent_sel(sli, run_id, is_5xx)}[{rng}]))"
    base = min(sli.min_entity_events, MIN_ENTITY_EVENTS)
    q = f"((({bad}) or ({total}) * 0) / clamp_min({total}, 1)) and on (entity) (({total}) >= {base})"
    if sli.min_entity_events > MIN_ENTITY_EVENTS:
        hour = f"sum by (entity) (increase({ENTITY_COUNTER}{_ent_sel(sli, run_id)}[{ENTITY_ELIGIBILITY_WINDOW}]))"
        q = f"({q}) and on (entity) (({hour}) >= {sli.min_entity_events})"
    return q


def worst_entities_promql(sli: SLI, run_id: str, rng: str = "60m", k: int = 5) -> str:
    return f"topk({k}, {entity_bad_ratio_promql(sli, run_id, rng)})"


def bad_ratio_promql(sli: SLI, run_id: str, rng: str) -> str:
    """Share of bad events over `rng`; 0 (not 1) when there was no traffic. For grouped SLIs: the worst
    entity's share (with enough traffic to be meaningful)."""
    if sli.group_by:
        return f"(max({entity_bad_ratio_promql(sli, run_id, rng)}) or vector(0))"
    if sli.kind == "window":
        return window_bad_ratio_promql(sli, run_id, rng)
    good, total = good_total_promql(sli, run_id, rng)
    good, total = f"({good} or vector(0))", f"({total} or vector(0))"
    return f"(({total} - {good}) / clamp_min({total}, 1))"


def burn_rate_promql(sli: SLI, run_id: str, rng: str) -> str:
    """Error-budget burn rate over `rng`: 1.0 means burning exactly at the budgeted pace."""
    return f"({bad_ratio_promql(sli, run_id, rng)} / {(100 - sli.target) / 100:.6g})"


def multiwindow_burn_promql(sli: SLI, run_id: str, short: str, long: str) -> str:
    """min(burn over short, burn over long): exceeds X only when BOTH windows burn faster than X.
    The long window proves the burn is significant; the short window makes the alert reset quickly
    once the problem is fixed."""
    a = f'label_replace({burn_rate_promql(sli, run_id, short)}, "w", "{short}", "", "")'
    b = f'label_replace({burn_rate_promql(sli, run_id, long)}, "w", "{long}", "", "")'
    return f"min({a} or {b})"


def budget_remaining_promql(sli: SLI, run_id: str, rng: str = "60m") -> str:
    """Fraction of a rolling `rng` error budget left (1 = untouched, 0 = exhausted, negative = overspent)."""
    return f"(1 - {burn_rate_promql(sli, run_id, rng)})"


# ---------------------------------------------------------------- local scoring

def evaluate_grouped(records, sli: SLI, start_ts=None, end_ts=None, min_events: int | None = None) -> dict:
    """Per-entity attainment for a grouped SLI over ingress records (both conditions, same method)."""
    good, total = {}, {}
    for rec in records:
        ts = rec.get("ts_epoch")
        if (start_ts is not None and ts is not None and ts < start_ts) or (end_ts is not None and ts is not None and ts >= end_ts):
            continue
        if rec.get("service") != sli.service or sli.group_by not in rec:
            continue
        if sli.routes and rec.get("route") not in sli.routes:
            continue
        e = rec[sli.group_by]
        code = int(rec.get("code") or 0)
        total[e] = total.get(e, 0) + 1
        good[e] = good.get(e, 0) + (0 < code < 500)
    att = {e: 100.0 * good[e] / total[e] for e in total}
    min_events = sli.min_entity_events if min_events is None else min_events
    eligible = {e: a for e, a in att.items() if total[e] >= min_events}
    below = sorted((a, e) for e, a in eligible.items() if a < sli.target)
    return {
        "name": sli.name, "criticality": sli.criticality, "target": sli.target, "group_by": sli.group_by,
        "entities_seen": len(total), "min_entity_events": min_events, "entities_with_min_events": len(eligible),
        "entities_below_target": len(below),
        "worst": [{"entity": e, "attainment_pct": round(a, 2), "events": total[e]} for a, e in below[:15]],
        "min_attainment_pct": round(min(eligible.values()), 2) if eligible else None,
        "aggregate_attainment_pct": round(100.0 * sum(good.values()) / sum(total.values()), 4) if total else None,
        # uniform keys with the aggregate SLIs: "met" = every entity with enough traffic meets the target
        "attainment_pct": round(min(eligible.values()), 4) if eligible else None,
        "error_budget_remaining_ratio": round(1 - (100 - min(eligible.values())) / (100 - sli.target), 4) if eligible else None,
        "met": (len(below) == 0) if eligible else None,
    }


def record_matches(sli: SLI, rec: dict) -> bool:
    if rec.get("service") != sli.service:
        return False
    if sli.routes:
        return rec.get("route") in sli.routes
    return rec.get("route") not in ("GET /healthz", "GET /internal/info")


def record_is_good(sli: SLI, rec: dict) -> bool:
    if sli.kind == "availability":
        code = int(rec.get("code") or 0)
        if code >= 500 or code == 0:
            return False
        return rec.get("reason") not in sli.bad_reasons
    return float(rec.get("duration_ms") or 0) <= sli.threshold_s * 1000.0


def evaluate_window(records, sli: SLI, start_ts=None, end_ts=None) -> dict:
    """Share of good one-minute windows (epoch-aligned), from ingress records. Minutes with no matching
    traffic do not count, as in the Coralogix SLO (missing data uncounted)."""
    mins: dict = {}
    for rec in records:
        ts = rec.get("ts_epoch")
        if ts is None or (start_ts is not None and ts < start_ts) or (end_ts is not None and ts >= end_ts):
            continue
        if not record_matches(sli, rec):
            continue
        code = int(rec.get("code") or 0)
        m = mins.setdefault(int(ts // 60), {"n": 0, "bad": 0, "dur": []})
        m["n"] += 1
        m["bad"] += (code >= 500 or code == 0)
        if sli.window_stat == "p95" and (sli.routes != ("POST /api/checkout",) or 200 <= code < 300):
            m["dur"].append(float(rec.get("duration_ms") or 0) / 1000.0)
    good = total = 0
    for m in mins.values():
        if sli.window_stat == "p95":
            if not m["dur"]:
                continue
            v = sorted(m["dur"])
            stat = v[min(len(v) - 1, int(0.95 * len(v)))]
        else:
            stat = m["bad"] / m["n"]
        total += 1
        good += stat <= sli.window_threshold
    attainment = (100.0 * good / total) if total else None
    remaining = None if attainment is None else 1.0 - (100.0 - attainment) / (100.0 - sli.target)
    return {"name": sli.name, "criticality": sli.criticality, "target": sli.target, "slo_type": "window",
            "good": good, "total": total,
            "attainment_pct": None if attainment is None else round(attainment, 4),
            "error_budget_remaining_ratio": None if remaining is None else round(remaining, 4),
            "met": None if attainment is None else attainment >= sli.target}


def evaluate_records(records, start_ts: float | None = None, end_ts: float | None = None) -> dict:
    """Compute every SLI over ingress.jsonl records (dicts with ts epoch seconds)."""
    out = {}
    for sli in MEASURED_SLIS:
        if sli.group_by:
            out[sli.key] = evaluate_grouped(records, sli, start_ts, end_ts)
            continue
        if sli.kind == "window":
            out[sli.key] = evaluate_window(records, sli, start_ts, end_ts)
            continue
        good = total = 0
        for rec in records:
            ts = rec.get("ts_epoch")
            if start_ts is not None and ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts is not None and ts >= end_ts:
                continue
            if record_matches(sli, rec):
                total += 1
                good += record_is_good(sli, rec)
        attainment = (100.0 * good / total) if total else None
        budget = (100.0 - sli.target)
        remaining = None if attainment is None else 1.0 - (100.0 - attainment) / budget
        out[sli.key] = {
            "name": sli.name,
            "criticality": sli.criticality,
            "target": sli.target,
            "good": good,
            "total": total,
            "attainment_pct": None if attainment is None else round(attainment, 4),
            "error_budget_remaining_ratio": None if remaining is None else round(remaining, 4),
            "met": None if attainment is None else attainment >= sli.target,
        }
    return out
