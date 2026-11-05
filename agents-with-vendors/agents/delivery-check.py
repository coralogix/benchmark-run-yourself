#!/usr/bin/env python3
"""Checks that the collector delivered everything each platform's pipelines kept
over a UTC window, from the collector's own counters in the demo's Prometheus.

  agents/delivery-check.py <start-utc> <end-utc>

Fails (exit 1) when, inside the window, any of these increased:
  - spans, log records or metric points a platform's exporter failed to send
    (after its retries) or could not queue (queue full);
  - data a receiver refused (the memory limiter refuses at the receiver);
  - traces a tail sampler had to decide before they were complete.
Sampling and the filters in otelcol-config-extras.yml are the only drops
allowed. Prints the exported counts per platform as well, so a platform that
received nothing shows up even when no counter moved.
"""
import json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def setting(name, default):
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
EXPORTERS = {"coralogix": "coralogix", "datadog": "datadog", "dynatrace": "otlphttp/dynatrace",
             "grafana": "otlphttp/grafana", "newrelic": "otlphttp/newrelic"}
SIGNALS = ("spans", "log_records", "metric_points")
EXPORTER_LOSSES = ["otelcol_exporter_send_failed_{s}_total", "otelcol_exporter_enqueue_failed_{s}_total"]
RECEIVER_LOSSES = ["otelcol_receiver_refused_{s}_total", "otelcol_processor_refused_{s}_total"]
SAMPLER_LOSSES = ["otelcol_processor_tail_sampling_sampling_trace_dropped_too_early_total"]


def query(expr, at):
    u = PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": at})
    return json.load(urllib.request.urlopen(u, timeout=120))["data"]["result"]


def increase(metric, labels, rng, at, by=""):
    sel = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
    grp = f" by ({by})" if by else ""
    return [(r["metric"].get(by, ""), float(r["value"][1]))
            for r in query(f"sum{grp}(increase({metric}{sel}[{rng}]))", at)]


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    t0, t1 = (datetime.fromisoformat(a.replace("Z", "+00:00")).astimezone(timezone.utc) for a in sys.argv[1:])
    secs = int((t1 - t0).total_seconds())
    rng, at = f"{secs}s", t1.timestamp()
    problems = []

    print(f"window {sys.argv[1]} to {sys.argv[2]} ({secs // 60} min)")
    print(f"{'platform':10} {'spans':>12} {'log records':>12} {'metric points':>14}")
    for p, e in EXPORTERS.items():
        sent = [sum(v for _, v in increase(f"otelcol_exporter_sent_{s}_total", {"exporter": e}, rng, at)) for s in SIGNALS]
        print(f"{p:10} {sent[0]:12.0f} {sent[1]:12.0f} {sent[2]:14.0f}")
        if sum(sent) == 0:
            problems.append(f"{p}: exported nothing")
        for tmpl in EXPORTER_LOSSES:
            for s in SIGNALS:
                n = sum(v for _, v in increase(tmpl.format(s=s), {"exporter": e}, rng, at))
                if n > 0.5:
                    problems.append(f"{p}: {tmpl.format(s=s)} +{n:.0f}")
    for tmpl in RECEIVER_LOSSES:
        for s in SIGNALS:
            for who, n in increase(tmpl.format(s=s), {}, rng, at, by="receiver" if "receiver" in tmpl else "processor"):
                if n > 0.5:
                    problems.append(f"{who or 'collector'}: {tmpl.format(s=s)} +{n:.0f}")
    for metric in SAMPLER_LOSSES:
        for who, n in increase(metric, {}, rng, at, by="processor"):
            if n > 0.5:
                problems.append(f"{who or 'tail sampler'}: {metric} +{n:.0f}")

    if problems:
        print("DELIVERY FAILED")
        for line in problems:
            print("  " + line)
        sys.exit(1)
    print("DELIVERY OK: nothing refused, overflowed, failed or decided early")


if __name__ == "__main__":
    main()
