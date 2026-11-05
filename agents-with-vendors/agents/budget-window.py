#!/usr/bin/env python3
"""Budget share per platform over a UTC window, from the demo's local Prometheus collector counters.

The volume each platform receives as a share of Coralogix's, priced per signal
with the share table, combined with Coralogix's spend split. The table, split
and Dynatrace billing factor are read from config/budget.json (or the file
named by BUDGET_CONFIG).

  agents/budget-window.py <start-utc> <end-utc> [--label=fault]
  agents/budget-window.py 2026-01-01T10:00:00Z 2026-01-01T10:30:00Z

Run it over a no-fault window for the retained shares, and over each fault
window (--label=fault) for each platform's spend during the incident.

Units: spans and log records are counts. Metrics: Dynatrace = datapoints x
billing factor; New Relic = datapoints; Grafana and Datadog = series seen in
the window (local Prometheus, with each platform's metric filters emulated;
histogram buckets counted as series). Datadog spans count only the sampled
`datadog` exporter, not the unsampled APM-stats connector. Containers named
by NON_SHOP_CONTAINERS (a regex, optional) are excluded from the series
counts; stop other containers on the host instead where you can.
"""
import json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def setting(name, default):
    """From the environment, else the folder's .env, else the default."""
    if os.environ.get(name):
        return os.environ[name]
    env = os.path.join(HERE, "..", ".env")
    if os.path.exists(env):
        for line in open(env):
            k, _, v = line.strip().partition("=")
            if k == name and v.strip().strip("'\""):
                return v.strip().strip("'\"")
    return default


PROM = setting("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
CONFIG = setting("BUDGET_CONFIG", os.path.join(HERE, "..", "config", "budget.json"))
EXP = {"coralogix": "coralogix", "dynatrace": "otlphttp/dynatrace", "grafana": "otlphttp/grafana",
       "newrelic": "otlphttp/newrelic", "datadog": "datadog"}
NON_SHOP = setting("NON_SHOP_CONTAINERS", "")
# Metric-name prefixes each platform's collector filters drop (Prometheus names).
CUT_COMMON = r"otelcol_|jvm_|process_runtime_|dotnet_gc_|v8js_|nodejs_eventloop_|go_memory_|db_sql_|db_client_|dns_lookup_|http_client_|rpc_client_|otel_sdk_|datadog_trace_agent|feature_flag"
CUT = {"grafana": CUT_COMMON, "datadog": CUT_COMMON + r"|traces_span_|system_|container_"}
ALWAYS_SKIP = r"target_info|scrape_|up$|prometheus_|ALERTS"


def load_config(path):
    cfg = json.load(open(path))
    missing = [f"split.{k}" for k in ("T", "L", "M") if cfg["split"].get(k) is None]
    missing += [f"table.{p}.{k}" for p, row in cfg["table"].items() for k in ("T", "L", "M") if row.get(k) is None]
    if cfg.get("dt_billing_factor") is None:
        missing.append("dt_billing_factor")
    if missing:
        sys.exit(f"{path}: set these from the pricing step first: {', '.join(missing)}")
    return cfg["split"], cfg["table"], float(cfg["dt_billing_factor"])


def q(expr, at):
    u = PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": at})
    return json.load(urllib.request.urlopen(u, timeout=120))["data"]["result"]


def inc(metric, exporter, rng, at):
    r = q(f'sum(increase({metric}{{exporter="{exporter}"}}[{rng}]))', at)
    return float(r[0]["value"][1]) if r else 0.0


def series(t0, t1, cut):
    """Distinct metric series seen in the window, after a platform's filters
    (Prometheus /api/v1/series; NON_SHOP_CONTAINERS excluded)."""
    u = PROM + "/api/v1/series?" + urllib.parse.urlencode(
        {"match[]": '{__name__=~".+"}', "start": t0, "end": t1})
    rows = json.load(urllib.request.urlopen(u, timeout=300))["data"]
    n = 0
    for r in rows:
        name = r["__name__"]
        if re.match(ALWAYS_SKIP, name) or (cut and re.match(cut, name)):
            continue
        if NON_SHOP and re.fullmatch(NON_SHOP, r.get("container_name", "")):
            continue
        n += 1
    return n


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    label = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--label=")), "")
    if len(args) != 2:
        sys.exit(__doc__)
    split, table, dt_factor = load_config(CONFIG)
    t0, t1 = (datetime.fromisoformat(a.replace("Z", "+00:00")).astimezone(timezone.utc) for a in args)
    secs = int((t1 - t0).total_seconds())
    rng, at = f"{secs}s", t1.timestamp()

    vol = {}
    for p, e in EXP.items():
        spans = inc("otelcol_exporter_sent_spans_total", e, rng, at) + inc("otelcol_exporter_send_failed_spans_total", e, rng, at)
        logs = inc("otelcol_exporter_sent_log_records_total", e, rng, at) + inc("otelcol_exporter_send_failed_log_records_total", e, rng, at)
        pts = inc("otelcol_exporter_sent_metric_points_total", e, rng, at) + inc("otelcol_exporter_send_failed_metric_points_total", e, rng, at)
        vol[p] = {"T": spans, "L": logs, "Mpts": pts}
    cx_series = series(t0.timestamp(), at, None)
    cx = vol["coralogix"]

    print(f"window {args[0]} to {args[1]} ({secs // 60} min) {label}")
    print(f"Coralogix: spans {cx['T']:.0f}, logs {cx['L']:.0f}, metric points {cx['Mpts']:.0f}, series {cx_series}")
    print(f"{'platform':10} {'spans%':>7} {'logs%':>7} {'metrics%':>9} {'spend % of budget':>18}")
    for p in table:
        v = vol[p]
        sT = v["T"] / cx["T"] if cx["T"] else 0
        sL = v["L"] / cx["L"] if cx["L"] else 0
        if p == "dynatrace":
            sM = v["Mpts"] * dt_factor / cx["Mpts"] if cx["Mpts"] else 0
        elif p == "newrelic":
            sM = v["Mpts"] / cx["Mpts"] if cx["Mpts"] else 0
        else:
            sM = series(t0.timestamp(), at, CUT[p]) / cx_series if cx_series else 0
        tb = table[p]
        spend = split["T"] * sT / tb["T"] + split["L"] * sL / tb["L"] + split["M"] * sM / tb["M"]
        print(f"{p:10} {100*sT:7.1f} {100*sL:7.1f} {100*sM:9.1f} {100*spend:18.0f}")


if __name__ == "__main__":
    main()
