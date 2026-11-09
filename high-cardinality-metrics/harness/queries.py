"""
The ten-rung query ladder, in increasing order of work the engine must do.

Rung shapes follow Robust Perception, PromLabs, kubernetes-mixin,
awesome-prometheus-alerts, the Google SRE Workbook, Cortex's PromQL deep-dive
and the Mimir query-sharding docs.

Metric names differ per backend, because each backend's loader rewrites the
counter name differently. The verify phase checks that every rung returns data.
"""

import os

# The generator emits the OTel counter as `http_requests_total_<band>`. Each
# backend's OTLP receiver then rewrites it differently, so this cannot be
# expressed as a single suffix:
#
#   coralogix   appends _total: http_requests_total_b4_total
#   mimir       loaded from Prometheus blocks with mimirtool, carries the
#               Prometheus name: http_requests_b4_total
#   prometheus  strips the inner _total and re-appends it at the end, under its
#               default UnderscoreEscapingWithSuffixes translation strategy:
#               http_requests_b4_total
COUNTER_NAME = {
    "coralogix":  "http_requests_total_{band}_total",
    # infra/stage.sh sets MIMIR_COUNTER_NAME to the same value.
    "mimir":      os.environ.get("MIMIR_COUNTER_NAME") or "http_requests_{band}_total",
    # ClickHouse TimeSeries, queried as a Prometheus API and loaded by metricgen
    # tsinsert, keeps the generator's name http_requests_total_<band>;
    # infra/stage.sh sets PROM_COUNTER_NAME for that client.
    "prometheus": os.environ.get("PROM_COUNTER_NAME") or "http_requests_{band}_total",
    # VictoriaMetrics imports the Prometheus blocks with vmctl, so it carries the
    # Prometheus name.
    "victoriametrics": "http_requests_{band}_total",
}

# Rungs. {c}=counter, {h}=histogram bucket base, {g}=gauge, {i}=info.
# Parameters in <angle brackets> are substituted per execution from real label
# values sampled out of the data, and rotated across repeats so no two runs
# measure the same warm cache entry.
RUNGS = [
    dict(
        id="R01_pinned", kind="instant",
        desc="One fully-pinned series. Pure index lookup, negligible scan.",
        q='{c}{{cluster="<cluster>",namespace="<namespace>",service="<service>",'
          'pod="<pod>",region="<region>",http_route="<route>",http_method="<method>",'
          'status_code="<status>",customer_id="<customer>"}}',
    ),
    dict(
        id="R02_label_values", kind="label_values",
        desc="Dashboard variable: enumerate every pod in the band, with no narrowing matcher. Index only, no samples.",
        label="pod", match='{c}',
    ),
    dict(
        id="R03_filter_eq", kind="instant",
        desc="Equality matcher set, thousands of series out. Materialisation cost.",
        q='{c}{{cluster="<cluster>",service="<service>",status_code="500"}}',
    ),
    dict(
        id="R04_filter_regex", kind="instant",
        desc="Regex and negated matchers.",
        q='{c}{{cluster="<cluster>",service="<service>",pod=~"<servicepfx>.*",'
          'status_code!~"2..",http_method=~"POST|PUT|PATCH"}}',
    ),
    dict(
        id="R05_group_by_gauge", kind="instant",
        desc="sum by on a gauge, with no windowed arithmetic.",
        q='sum by (service) ({g}{{cluster="<cluster>"}})',
    ),
    dict(
        id="R06_rate_group_by", kind="instant",
        desc="Per-series windowed rate, then fan-in.",
        q='sum by (service) (rate({c}{{cluster="<cluster>"}}[5m]))',
    ),
    dict(
        id="R07_topk", kind="instant",
        desc="Ranking across every output group.",
        q='topk(10, sum by (pod) (rate({c}{{cluster="<cluster>"}}[5m])))',
    ),
    dict(
        id="R08_histogram_quantile", kind="instant",
        desc="p99. 14 series per label set, rate over all buckets, fan-in preserving le, interpolation.",
        q='histogram_quantile(0.99, sum by (le, service) (rate({h}_bucket{{cluster="<cluster>"}}[5m])))',
    ),
    dict(
        id="R09_join_group_left", kind="instant",
        desc="Many-to-one join onto the info metric to recover `node`. Pre-aggregated left side, topk guard against duplicate match groups.",
        q='sum by (node) ('
          'sum by (cluster, namespace, pod) (rate({c}{{cluster="<cluster>"}}[5m]))'
          ' * on (cluster, namespace, pod) group_left (node) '
          'topk by (cluster, namespace, pod) (1, max by (cluster, namespace, pod, node) ({i}{{node!=""}}))'
          ')',
    ),
    dict(
        id="R10_burn_rate", kind="instant",
        desc="Multi-window multi-burn-rate SLO alert (SRE Workbook). Eight rate pipelines, four divisions, two ANDs, one OR union.",
        q='('
          '(sum by (service) (rate({c}{{status_code=~"5.."}}[5m])) / sum by (service) (rate({c}[5m]))) > (14.4 * 0.001)'
          ' and '
          '(sum by (service) (rate({c}{{status_code=~"5.."}}[1h])) / sum by (service) (rate({c}[1h]))) > (14.4 * 0.001)'
          ')'
          ' or '
          '('
          '(sum by (service) (rate({c}{{status_code=~"5.."}}[30m])) / sum by (service) (rate({c}[30m]))) > (6 * 0.001)'
          ' and '
          '(sum by (service) (rate({c}{{status_code=~"5.."}}[6h])) / sum by (service) (rate({c}[6h]))) > (6 * 0.001)'
          ')',
    ),
]


def metric_names(backend, band):
    return {
        "c": COUNTER_NAME[backend].format(band=band),
        "h": f"http_request_duration_seconds_{band}",
        "g": f"container_memory_working_set_bytes_{band}",
        "i": f"kube_pod_info_{band}",
    }


def render(rung, backend, band, params):
    names = metric_names(backend, band)
    def sub(t):
        t = t.format(**names)
        for k, v in params.items():
            t = t.replace(f"<{k}>", str(v))
        return t
    out = dict(rung)
    if "q" in out:
        out["q"] = sub(out["q"])
    if "match" in out:
        out["match"] = sub(out["match"])
    return out
