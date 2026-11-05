"""Coralogix asset definitions (dashboards, alerts, SLOs) for one benchmark run.

Everything is scoped to a single run with the `bench_run_id` label, so assets
created for a run only ever see that run's telemetry, and error budgets start
fresh at the beginning of every trial.

Asset classes:
  common  - deployed to BOTH conditions' accounts (ordinary observability)
  slo     - deployed ONLY to the SLO-guided account
"""
from __future__ import annotations

import secrets
import string
import uuid

from harness.cx import sli as sli_mod

MANAGED_LABEL = "larder-bench"
DASHBOARD_FOLDER = "Larder benchmark"
C = sli_mod.INGRESS_COUNTER
H = sli_mod.INGRESS_HIST


def _uuid() -> dict:
    return {"value": str(uuid.uuid4())}


def _nanoid(n: int = 21) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ------------------------------------------------------------------ widgets

def line(title: str, queries: list[tuple[str, str]], unit: str = "UNIT_UNSPECIFIED") -> dict:
    """queries: list of (legend-ish name, promql)."""
    defs = []
    for name, q in queries:
        defs.append({
            "id": str(uuid.uuid4()),
            "name": name,
            "query": {"metrics": {
                "promqlQuery": {"value": q},
                "filters": [],
                "editorMode": "METRICS_QUERY_EDITOR_MODE_TEXT",
                "seriesLimitType": "METRICS_SERIES_LIMIT_TYPE_BY_SERIES_COUNT",
            }},
            "seriesCountLimit": "20",
            "unit": unit,
            "scaleType": "SCALE_TYPE_LINEAR",
            "isVisible": True,
            "colorScheme": "classic",
            "resolution": {"bucketsPresented": 120},
            "dataModeType": "DATA_MODE_TYPE_HIGH_UNSPECIFIED",
            "decimal": 2,
            "hashColors": False,
            "decimalPrecision": False,
            "intervalResolution": {"auto": {"minimumInterval": "15s", "maximumDataPoints": 120}},
        })
    return {"id": _uuid(), "title": title, "definition": {"lineChart": {
        "legend": {"isVisible": True, "columns": [], "groupByQuery": True, "placement": "LEGEND_PLACEMENT_AUTO"},
        "tooltip": {"showLabels": False, "type": "TOOLTIP_TYPE_ALL"},
        "queryDefinitions": defs,
        "stackedLine": "STACKED_LINE_UNSPECIFIED",
        "connectNulls": False,
    }}}


def gauge(title: str, q: str, unit: str = "UNIT_NUMBER", lo: float = 0, hi: float = 100,
          thresholds: list[tuple[float, str]] | None = None, decimals: int = 2) -> dict:
    thresholds = thresholds or [(lo, "green")]
    return {"id": _uuid(), "title": title, "definition": {"gauge": {
        "query": {"metrics": {
            "promqlQuery": {"value": q},
            "aggregation": "AGGREGATION_UNSPECIFIED",
            "filters": [],
            "editorMode": "METRICS_QUERY_EDITOR_MODE_TEXT",
            "promqlQueryType": "PROM_QL_QUERY_TYPE_INSTANT",
        }},
        "min": lo, "max": hi,
        "showInnerArc": True, "showOuterArc": True,
        "unit": unit,
        "thresholds": [{"from": f, "color": f"var(--c-visualization-{c}-05)"} for f, c in thresholds],
        "dataModeType": "DATA_MODE_TYPE_HIGH_UNSPECIFIED",
        "thresholdBy": "THRESHOLD_BY_UNSPECIFIED",
        "decimal": decimals,
        "thresholdType": "THRESHOLD_TYPE_ABSOLUTE",
        "legend": {"isVisible": True, "columns": [], "groupByQuery": True, "placement": "LEGEND_PLACEMENT_AUTO"},
        "legendBy": "LEGEND_BY_GROUPS",
        "displaySeriesName": False,
        "decimalPrecision": False,
    }}}


def row(*widgets, height: int = 19) -> dict:
    return {"id": _uuid(), "appearance": {"height": height}, "widgets": list(widgets)}


def section(name: str, rows: list[dict]) -> dict:
    return {"id": _uuid(), "rows": rows, "options": {"custom": {
        "name": name, "collapsed": False, "color": {"predefined": "SECTION_PREDEFINED_COLOR_UNSPECIFIED"}}}}


def dashboard(name: str, description: str, sections: list[dict], timeframe_s: int = 3600) -> dict:
    return {
        "id": _nanoid(),
        "name": name,
        "description": description,
        "layout": {"sections": sections},
        "variables": [], "variablesV2": [], "filters": [],
        "relativeTimeFrame": f"{timeframe_s}s",
        "annotations": [], "off": {}, "actions": [],
    }


# ------------------------------------------------------------------ dashboards

def common_dashboards(run_id: str) -> list[dict]:
    r = f'bench_run_id="{run_id}"'
    edge = f'service="edge-api",{r}'
    internal = f'service!="edge-api",{r}'
    w = "[2m]"

    overview = dashboard(
        "Larder · Service overview",
        f"Customer edge and internal mesh traffic for run {run_id}: throughput, errors, latency, outage signals.",
        [
            section("Customer edge (edge-api on :8000)", [
                row(
                    line("Requests/s by status class", [("rps", f"sum by (code_class) (rate({C}{{{edge}}}{w}))")]),
                    line("5xx error ratio (%)", [("5xx %", f"100 * sum(rate({C}{{{edge},code_class=\"5xx\"}}{w})) / clamp_min(sum(rate({C}{{{edge}}}{w})), 0.001)")], "UNIT_PERCENT"),
                ),
                row(
                    line("Latency p50 / p95 / p99 (s)", [
                        ("p50", f"histogram_quantile(0.50, sum by (le) (rate({H}_bucket{{{edge}}}{w})))"),
                        ("p95", f"histogram_quantile(0.95, sum by (le) (rate({H}_bucket{{{edge}}}{w})))"),
                        ("p99", f"histogram_quantile(0.99, sum by (le) (rate({H}_bucket{{{edge}}}{w})))"),
                    ], "UNIT_SECONDS"),
                    line("p95 latency by route (s)", [("p95", f"histogram_quantile(0.95, sum by (le, route) (rate({H}_bucket{{{edge}}}{w})))")], "UNIT_SECONDS"),
                ),
                row(
                    line("Non-2xx responses/s by route, code and reason", [("errors", f"sum by (route, code, reason) (rate({C}{{{edge},code_class!=\"2xx\"}}{w}))")]),
                    line("Checkout responses/s by code and reason", [("checkout", f"sum by (code, reason) (rate({C}{{{edge},route=\"POST /api/checkout\"}}{w}))")]),
                ),
            ]),
            section("Internal services (mesh)", [
                row(
                    line("Requests/s by service", [("rps", f"sum by (service) (rate({C}{{{internal}}}{w}))")]),
                    line("5xx/s by service and route", [("5xx", f"sum by (service, route, code) (rate({C}{{{internal},code_class=\"5xx\"}}{w}))")]),
                ),
                row(
                    line("p95 latency by service (s)", [("p95", f"histogram_quantile(0.95, sum by (le, service) (rate({H}_bucket{{{internal}}}{w})))")], "UNIT_SECONDS"),
                    line("Upstream failures/s (502 refused, 504 timeout) by service", [("upstream", f"sum by (service, code) (rate({C}{{{r},code=~\"502|504\"}}{w}))")]),
                ),
                row(
                    line("Order confirmation responses/s by code and reason", [("orders", f"sum by (code, reason) (rate({C}{{service=\"orders\",{r},route=\"POST /internal/orders\"}}{w}))")]),
                    line("p95 latency by route for orders and payments (s)", [("p95", f"histogram_quantile(0.95, sum by (le, service, route) (rate({H}_bucket{{service=~\"orders|payments\",{r}}}{w})))")], "UNIT_SECONDS"),
                ),
            ]),
        ],
    )

    runtime = dashboard(
        "Larder · Runtime and dependencies",
        f"Process health, restarts, resource usage and application internals for run {run_id}.",
        [
            section("Processes (all 21 services)", [
                row(
                    line("Services not healthy (count)", [("down", f"count(larder_service_up{{{r}}} == 0) or vector(0)")]),
                    line("Restarts (cumulative, by service and reason)", [("restarts", f"sum by (service, reason) (larder_service_restarts_total{{{r}}})")]),
                ),
                row(
                    line("CPU % by service", [("cpu", f"max by (service) (larder_process_cpu_percent{{{r}}})")], "UNIT_PERCENT"),
                    line("RSS bytes by service", [("rss", f"max by (service) (larder_process_rss_bytes{{{r}}})")], "UNIT_BYTES"),
                ),
            ]),
            section("commerce internals", [
                row(
                    line("Checkouts/s by outcome", [("checkouts", f"sum by (outcome) (rate(commerce_checkouts_total{{{r}}}{w}))")]),
                    line("Busy workers by service", [("busy", f"max by (service_name) (commerce_busy_workers{{{r}}})")]),
                ),
                row(
                    line("Search cache entries / flushes", [
                        ("entries", f"max(commerce_search_cache_entries{{{r}}})"),
                        ("flushes", f"sum(commerce_search_cache_flushes_total{{{r}}})"),
                    ]),
                    line("Outbound call p95 by caller and target (s)", [("p95", f"histogram_quantile(0.95, sum by (le, service_name, target) (rate(commerce_http_client_duration_seconds_bucket{{{r}}}{w})))")], "UNIT_SECONDS"),
                ),
                row(
                    line("Active carts", [("carts", f"max(commerce_active_carts{{{r}}})")]),
                    line("Coupon applications/s by result", [("coupons", f"sum by (result) (rate(commerce_coupon_applications_total{{{r}}}{w}))")]),
                ),
            ]),
            section("ops internals", [
                row(
                    line("Event loop lag by service (s)", [("lag", f"max by (service_name) (ops_event_loop_lag_seconds{{{r}}})")], "UNIT_SECONDS"),
                    line("Slot DB pool in use / waiting", [
                        ("in_use", f"max(ops_db_pool_in_use{{{r}}})"),
                        ("waiting", f"max(ops_db_pool_waiting{{{r}}})"),
                    ]),
                ),
                row(
                    line("Bus queue depth by topic", [("bus", f"max by (topic) (ops_bus_queue_depth{{{r}}})")]),
                    line("Bus deliveries/s by subscriber and outcome", [("deliveries", f"sum by (subscriber, outcome) (rate(ops_bus_deliveries_total{{{r}}}{w}))")]),
                ),
                row(
                    line("Webhook queue depth", [("depth", f"max(ops_webhook_queue_depth{{{r}}})")]),
                    line("Webhook deliveries/s by outcome", [("deliveries", f"sum by (outcome) (rate(ops_webhook_deliveries_total{{{r}}}{w}))")]),
                ),
                row(
                    line("Payment attempts/s by attempt and outcome", [("payments", f"sum by (attempt, outcome) (rate(ops_payment_attempts_total{{{r}}}{w}))")]),
                    line("Orders/s and reservations/s by outcome", [
                        ("orders", f"sum by (outcome) (rate(ops_orders_total{{{r}}}{w}))"),
                        ("reservations", f"sum by (outcome) (rate(ops_reservations_total{{{r}}}{w}))"),
                    ]),
                ),
                row(
                    line("Fraud decisions/s", [("fraud", f"sum by (decision) (rate(ops_fraud_decisions_total{{{r}}}{w}))")]),
                ),
            ]),
        ],
    )
    return [overview, runtime]


def slo_dashboards(run_id: str, slos: tuple | list | None = None) -> list[dict]:
    rows = []
    for s in (slos if slos is not None else sli_mod.SLIS):
        if s.group_by:
            worst = f"100 * max({sli_mod.entity_bad_ratio_promql(s, run_id, '${{__range}}')})"
            rows.append(row(
                gauge(f"{s.name.split('· ')[1]}: worst {s.group_by} error % (target ≤ {100 - s.target:g}%)",
                      worst, "UNIT_PERCENT", 0, 100, [(0, "green"), (100 - s.target, "red")], 1),
                line(f"Worst 5 {s.group_by}s: error ratio over 10m (≥{s.min_entity_events} requests in 60m)",
                     [("worst", sli_mod.worst_entities_promql(s, run_id, "10m", 5))], "UNIT_UNSPECIFIED"),
                height=17,
            ))
            continue
        good, total = sli_mod.good_total_promql(s, run_id, "${__range}")
        attainment = f"100 * ({good}) / clamp_min({total}, 1)"
        budget = f"100 * (1 - (1 - ({good}) / clamp_min({total}, 1)) / {(100 - s.target) / 100:.6g})"
        rows.append(row(
            gauge(f"{s.name.split('· ')[1]}: attainment % (target {s.target:g}%)", attainment, "UNIT_PERCENT", 90, 100,
                  [(90, "red"), (s.target, "green")], 3),
            gauge("Error budget remaining %", budget, "UNIT_PERCENT", -100, 100, [(-100, "red"), (0, "yellow"), (50, "green")], 1),
            line("Burn rate (2m / 10m / 60m; 1.0 = on budget)", [
                ("2m", sli_mod.burn_rate_promql(s, run_id, "2m")),
                ("10m", sli_mod.burn_rate_promql(s, run_id, "10m")),
                ("60m", sli_mod.burn_rate_promql(s, run_id, "60m")),
            ]),
            height=17,
        ))
    return [dashboard(
        "Larder · SLOs and error budgets",
        f"Attainment, remaining error budget and burn rate for every Larder SLO in run {run_id}. "
        "Use the dashboard time picker to set the evaluation window (default: last hour).",
        [section("Service level objectives", rows)],
    )]


# ------------------------------------------------------------------ alerts

def _metric_alert(name: str, desc: str, priority: str, promql: str, threshold: float, minutes: int,
                  run_id: str, less_than: bool = False, for_over_pct: int = 100) -> dict:
    return {
        "name": name,
        "description": desc,
        "enabled": True,
        "priority": f"ALERT_DEF_PRIORITY_{priority}",
        "type": "ALERT_DEF_TYPE_METRIC_THRESHOLD",
        "metricThreshold": {
            "metricFilter": {"promql": promql},
            "rules": [{
                "condition": {
                    "threshold": threshold,
                    "forOverPct": for_over_pct,
                    "ofTheLast": {"metricTimeWindowSpecificValue": f"METRIC_TIME_WINDOW_VALUE_MINUTES_{minutes}"},
                    "conditionType": "METRIC_THRESHOLD_CONDITION_TYPE_LESS_THAN" if less_than else "METRIC_THRESHOLD_CONDITION_TYPE_MORE_THAN_OR_UNSPECIFIED",
                },
                "override": {"priority": f"ALERT_DEF_PRIORITY_{priority}"},
            }],
            "missingValues": {"replaceWithZero": not less_than},
        },
        "groupByKeys": [],
        "incidentsSettings": {"minutes": 10, "notifyOn": "NOTIFY_ON_TRIGGERED_AND_RESOLVED"},
        "notificationGroup": {"groupByKeys": [], "webhooks": [], "destinations": []},
        "entityLabels": {"managed_by": MANAGED_LABEL, "bench_run_id": run_id, "asset_class": "common"},
        "phantomMode": False,
    }


def _checkout_failure_pct(sf: str, w: str) -> str:
    co = f'{sf},route="POST /api/checkout"'
    failed = f'sum(rate({C}{{{co},code_class="5xx"}}{w}) or vector(0)) + sum(rate({C}{{{co},reason="price_mismatch"}}{w}) or vector(0))'
    return f"100 * ({failed}) / clamp_min(sum(rate({C}{{{co}}}{w})), 0.001)"


def common_alerts(run_id: str) -> list[dict]:
    r = f'bench_run_id="{run_id}"'
    edge = f'service="edge-api",{r}'
    internal = f'service!="edge-api",{r}'
    w = "[5m]"
    return [
        _metric_alert(
            "Larder · Customer API 5xx error rate above 5%",
            "More than 5% of customer requests at the edge returned a 5xx (including edge 502/504) over 5 minutes.",
            "P2", f"100 * sum(rate({C}{{{edge},code_class=\"5xx\"}}{w})) / clamp_min(sum(rate({C}{{{edge}}}{w})), 0.001)", 5, 5, run_id),
        _metric_alert(
            "Larder · Checkout failure rate above 10%",
            "More than 10% of checkout attempts failed with a 5xx or a price_mismatch rejection over 5 minutes.",
            "P1", _checkout_failure_pct(edge, w), 10, 5, run_id),
        _metric_alert(
            "Larder · Customer API p95 latency above 2s",
            "Edge p95 latency for customer requests exceeded 2 seconds over 5 minutes.",
            "P2", f"histogram_quantile(0.95, sum by (le) (rate({H}_bucket{{{edge}}}{w})))", 2, 5, run_id),
        _metric_alert(
            "Larder · Service down",
            "At least one Larder service failed its health check for at least 2 of the last 5 minutes.",
            "P1", f"min(larder_service_up{{{r}}})", 1, 5, run_id, less_than=True, for_over_pct=40),
        _metric_alert(
            "Larder · Internal service 5xx rate above 5%",
            "More than 5% of calls into some internal service returned a 5xx over 5 minutes.",
            "P2", f"max(100 * sum by (service) (rate({C}{{{internal},code_class=\"5xx\"}}{w})) / clamp_min(sum by (service) (rate({C}{{{internal}}}{w})), 0.001))", 5, 5, run_id),
        _metric_alert(
            "Larder · Node event loop lag above 200ms",
            "Some ops (Node.js) service had event loop lag above 200 ms for 5 minutes.",
            "P3", f"max(ops_event_loop_lag_seconds{{{r}}})", 0.2, 5, run_id),
        _metric_alert(
            "Larder · Webhook queue depth above 300",
            "More than 300 partner webhook deliveries are waiting in the webhooks service queue.",
            "P3", f"max(ops_webhook_queue_depth{{{r}}})", 300, 5, run_id),
        _metric_alert(
            "Larder · Event bus backlog above 200",
            "More than 200 events are waiting for delivery on some event bus topic.",
            "P3", f"max(ops_bus_queue_depth{{{r}}})", 200, 5, run_id),
    ]


# ------------------------------------------------------------------ SLO alerts (SLO-guided account only)

# Multi-window burn-rate alerts, scaled to a rolling one-hour budget. Burn rate is measured against
# each SLO's target, so 1.0 = spending budget exactly at the sustainable pace.
#   fast burn: >= 10x over BOTH 2m and 10m: a rolling hour's budget gone in ~6 minutes (P1)
#   slow burn: >=  2x over BOTH 10m and 60m: sustained overspend (P2)
# Session budget: rolling 60-minute budget below 50% (P3) or exhausted (P2).
# Native SLO error-budget alerts: the Coralogix SLO engine's own (1-day window) budget, 50/25/0%.
FAST_BURN = ("2m", "10m", 10.0)
SLOW_BURN = ("10m", "60m", 2.0)


def _slo_alert_labels(run_id: str, key: str, kind: str) -> dict:
    return {"managed_by": MANAGED_LABEL, "bench_run_id": run_id, "asset_class": "slo", "sli": key, "slo_alert": kind}


def slo_alerts(run_id: str, slo_ids: dict[str, str], slos: tuple | list | None = None) -> list[dict]:
    out = []
    for s in (slos if slos is not None else sli_mod.SLIS):
        short_name = s.name.split("· ", 1)[1]
        for kind, (short, long, mult), prio in (("fast burn", FAST_BURN, "P1"), ("slow burn", SLOW_BURN, "P2")):
            a = _metric_alert(
                f"Larder · SLO · {short_name} · {kind}",
                f"{s.name} (target {s.target:g}%) is burning error budget at >= {mult:g}x the sustainable rate over both "
                f"the last {short} and the last {long}.",
                prio, sli_mod.multiwindow_burn_promql(s, run_id, short, long), mult, 5, run_id, for_over_pct=40)
            a["metricThreshold"]["missingValues"] = {"replaceWithZero": True}
            a["entityLabels"] = _slo_alert_labels(run_id, s.key, kind)
            out.append(a)
        b = _metric_alert(
            f"Larder · SLO · {short_name} · session budget",
            f"Rolling 60-minute error budget for {s.name}: P3 when below 50% remaining, P2 when exhausted.",
            "P3", sli_mod.budget_remaining_promql(s, run_id, "60m"), 0.5, 5, run_id, less_than=True, for_over_pct=40)
        b["metricThreshold"]["rules"].append({
            "condition": {"threshold": 0, "forOverPct": 40,
                          "ofTheLast": {"metricTimeWindowSpecificValue": "METRIC_TIME_WINDOW_VALUE_MINUTES_5"},
                          "conditionType": "METRIC_THRESHOLD_CONDITION_TYPE_LESS_THAN"},
            "override": {"priority": "ALERT_DEF_PRIORITY_P2"}})
        b["metricThreshold"]["missingValues"] = {"replaceWithZero": False}
        b["entityLabels"] = _slo_alert_labels(run_id, s.key, "session budget")
        out.append(b)
        if slo_ids.get(s.key):
            out.append({
                "name": f"Larder · SLO · {short_name} · error budget (SLO engine)",
                "description": f"Coralogix SLO engine: remaining 1-day error budget for {s.name}. Authoritative, evaluated with a delay of several minutes.",
                "enabled": True,
                "priority": "ALERT_DEF_PRIORITY_P3",
                "type": "ALERT_DEF_TYPE_SLO_THRESHOLD",
                "sloThreshold": {"sloDefinition": {"sloId": slo_ids[s.key]}, "errorBudget": {"rules": [
                    {"condition": {"threshold": 50}, "override": {"priority": "ALERT_DEF_PRIORITY_P3"}},
                    {"condition": {"threshold": 25}, "override": {"priority": "ALERT_DEF_PRIORITY_P2"}},
                    {"condition": {"threshold": 0}, "override": {"priority": "ALERT_DEF_PRIORITY_P1"}},
                ]}},
                "incidentsSettings": {"minutes": 10, "notifyOn": "NOTIFY_ON_TRIGGERED_AND_RESOLVED"},
                "notificationGroup": {"groupByKeys": [], "webhooks": [], "destinations": []},
                "entityLabels": _slo_alert_labels(run_id, s.key, "error budget"),
                "phantomMode": False,
            })
            # the SLO engine's own multi-window burn-rate alert: fires only when the burn rate is above the
            # threshold over both the long window (1 hour) and its short companion window. A burn rate cannot
            # exceed 1 / (1 - target) (every event bad), and the engine rejects thresholds above it, so loose
            # targets (the window SLOs) get proportionally lower thresholds.
            max_burn = 100.0 / (100.0 - s.target)
            p1, p2 = round(min(14.4, 0.8 * max_burn), 1), round(min(6.0, 0.6 * max_burn), 1)
            out.append({
                "name": f"Larder · SLO · {short_name} · burn rate (SLO engine)",
                "description": f"Coralogix SLO engine: dual-window burn rate for {s.name} over 1 hour and its short window: "
                               f"{p1:g}x or more is P1, {p2:g}x or more is P2.",
                "enabled": True,
                "priority": "ALERT_DEF_PRIORITY_P2",
                "type": "ALERT_DEF_TYPE_SLO_THRESHOLD",
                "sloThreshold": {"sloDefinition": {"sloId": slo_ids[s.key]}, "burnRate": {
                    "dual": {"timeDuration": {"duration": "1", "unit": "DURATION_UNIT_HOURS"}},
                    "rules": [{"condition": {"threshold": p1}, "override": {"priority": "ALERT_DEF_PRIORITY_P1"}},
                              {"condition": {"threshold": p2}, "override": {"priority": "ALERT_DEF_PRIORITY_P2"}}]}},
                "incidentsSettings": {"minutes": 10, "notifyOn": "NOTIFY_ON_TRIGGERED_AND_RESOLVED"},
                "notificationGroup": {"groupByKeys": [], "webhooks": [], "destinations": []},
                "entityLabels": _slo_alert_labels(run_id, s.key, "burn rate engine"),
                "phantomMode": False,
            })
    return out


# ------------------------------------------------------------------ SLOs

def slos(run_id: str, slos: tuple | list | None = None) -> list[dict]:
    out = []
    for s in (slos if slos is not None else sli_mod.SLIS):
        slo = {
            "name": s.name,
            "description": s.description,
            "labels": {"managed_by": MANAGED_LABEL, "bench_run_id": run_id, "sli": s.key,
                       "service": s.service, "criticality": s.criticality},
            "targetThresholdPercentage": s.target,
            "sloTimeFrame": "SLO_TIME_FRAME_1_DAY",
        }
        if s.kind == "window":  # time-slice SLO: the share of good one-minute windows
            slo["sloType"] = "SLO_TYPE_WINDOW"
            slo["windowBasedMetricSli"] = {"query": {"query": sli_mod.window_value_promql(s, run_id)},
                                           "window": "WINDOW_SLO_WINDOW_1_MINUTE",
                                           "comparisonOperator": "COMPARISON_OPERATOR_LESS_THAN_OR_EQUALS",
                                           "threshold": s.window_threshold,
                                           "missingDataStrategy": "MISSING_DATA_STRATEGY_UNCOUNTED"}
        else:
            good, total = sli_mod.good_total_promql(s, run_id)
            slo["sloType"] = "SLO_TYPE_REQUEST"
            slo["requestBasedMetricSli"] = {"goodEvents": {"query": good}, "totalEvents": {"query": total}}
        out.append(slo)
    return out
