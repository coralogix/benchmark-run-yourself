"""
queries_ch.py: the ten rungs translated from PromQL to ClickHouse SQL.

ClickHouse MergeTree does not speak PromQL. Each statement here answers the same
question over the same window, with the same grouping and output shape, as its
PromQL original in queries.py.

Schema is exporter-generated, read back verbatim with SHOW CREATE TABLE:

  otel.otel_metrics_sum    counters       (http_requests_total_<band>)
  otel.otel_metrics_gauge  gauges, histogram buckets, info
      ENGINE = MergeTree
      PARTITION BY toDate(TimeUnix)
      ORDER BY (ServiceName, MetricName, toStartOfHour(TimeUnix),
                cityHash64(Attributes), TimeUnix)
      bloom_filter skip indices on mapKeys/mapValues(Attributes)

Metric names are NOT rewritten by the clickhouseexporter, so they keep the
generator's form (http_requests_total_b4) and differ from Coralogix's and Prometheus's.

RATE: rate() is translated as the sum of positive deltas between consecutive
samples, divided by the window, which corrects for counter resets. It does not
extrapolate to the window edges.

NOTE on lagInFrame: it returns 0, not NULL, outside the frame, so the first
sample of every series would otherwise contribute a delta equal to the whole
counter value. toNullable(...)/NULL default plus an explicit ROWS frame makes
the first row NULL so sumIf skips it.
"""

DB = "otel"
SUM_T = f"{DB}.otel_metrics_sum"
GAUGE_T = f"{DB}.otel_metrics_gauge"

# Metric names: the exporter leaves them alone, so these are the generator's own.
def metric_names_ch(band):
    return {
        "c": f"http_requests_total_{band}",
        "h": f"http_request_duration_seconds_{band}",
        "g": f"container_memory_working_set_bytes_{band}",
        "i": f"kube_pod_info_{band}",
    }


# PromQL instant queries resolve each series to its latest sample within the
# 5m lookback-delta. Mirrored here as a 300s floor on TimeUnix plus argMax.
LOOKBACK_S = 300


def _rate_subquery(table, metric, extra_where, window_s):
    """Per-series rate over `window_s`, as the sum of positive deltas.

    Returns rows of (sid, svc, pod, cluster, namespace, r). Grouping columns are
    carried through so the caller can fan in on whichever it needs.
    """
    return f"""
    SELECT
      sid,
      any(svc) AS svc, any(pod) AS pod,
      any(cl) AS cl, any(ns) AS ns,
      sumIf(d, d > 0) / {window_s} AS r
    FROM (
      SELECT
        cityHash64(Attributes) AS sid,
        Attributes['service']   AS svc,
        Attributes['pod']       AS pod,
        Attributes['cluster']   AS cl,
        Attributes['namespace'] AS ns,
        Value - lagInFrame(toNullable(Value), 1, NULL) OVER (
          PARTITION BY cityHash64(Attributes) ORDER BY TimeUnix
          ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
        ) AS d
      FROM {table}
      WHERE MetricName = '{metric}'
        {extra_where}
        AND TimeUnix >= now() - INTERVAL {window_s} SECOND
    )
    GROUP BY sid"""


def build(rung_id, band, params, eval_ts=None):
    """Return the SQL for one rung. When eval_ts (unix seconds) is given, every
    now() anchor is pinned to that instant so the query evaluates at a fixed point
    inside the backfilled data window, exactly like PromQL's time= parameter on the
    other engines. Without live ingestion the data does not reach wall-clock now(),
    so an unpinned query would scan an empty recent window and return nothing."""
    sql = _build_sql(rung_id, band, params)
    if eval_ts:
        sql = sql.replace("now()", "fromUnixTimestamp(%d)" % int(eval_ts))
    return sql


def _build_sql(rung_id, band, params):
    """Return the SQL for one rung, with parameters already substituted."""
    n = metric_names_ch(band)
    c, g, h, i = n["c"], n["g"], n["h"], n["i"]
    p = params

    if rung_id == "R01_pinned":
        # PromQL: {c}{cluster=..,namespace=..,service=..,pod=..,region=..,
        #            http_route=..,http_method=..,status_code=..,customer_id=..}
        return f"""
SELECT argMax(Value, TimeUnix) AS v
FROM {SUM_T}
WHERE MetricName = '{c}'
  AND Attributes['cluster']     = '{p["cluster"]}'
  AND Attributes['namespace']   = '{p["namespace"]}'
  AND Attributes['service']     = '{p["service"]}'
  AND Attributes['pod']         = '{p["pod"]}'
  AND Attributes['region']      = '{p["region"]}'
  AND Attributes['http_route']  = '{p["route"]}'
  AND Attributes['http_method'] = '{p["method"]}'
  AND Attributes['status_code'] = '{p["status"]}'
  AND Attributes['customer_id'] = '{p["customer"]}'
  AND TimeUnix >= now() - INTERVAL {LOOKBACK_S} SECOND"""

    if rung_id == "R02_label_values":
        # PromQL: /api/v1/label/pod/values?match[]={c}
        # Unbounded in time, as the PromQL label values API is without start/end.
        return f"""
SELECT DISTINCT Attributes['pod'] AS pod
FROM {SUM_T}
WHERE MetricName = '{c}'
ORDER BY pod"""

    if rung_id == "R03_filter_eq":
        return f"""
SELECT cityHash64(Attributes) AS sid, argMax(Value, TimeUnix) AS v
FROM {SUM_T}
WHERE MetricName = '{c}'
  AND Attributes['cluster']     = '{p["cluster"]}'
  AND Attributes['service']     = '{p["service"]}'
  AND Attributes['status_code'] = '500'
  AND TimeUnix >= now() - INTERVAL {LOOKBACK_S} SECOND
GROUP BY sid"""

    if rung_id == "R04_filter_regex":
        # PromQL regex matchers are fully anchored, hence ^...$ on every match().
        return f"""
SELECT cityHash64(Attributes) AS sid, argMax(Value, TimeUnix) AS v
FROM {SUM_T}
WHERE MetricName = '{c}'
  AND Attributes['cluster'] = '{p["cluster"]}'
  AND Attributes['service'] = '{p["service"]}'
  AND match(Attributes['pod'], '^{p["servicepfx"]}.*$')
  AND NOT match(Attributes['status_code'], '^2..$')
  AND match(Attributes['http_method'], '^(POST|PUT|PATCH)$')
  AND TimeUnix >= now() - INTERVAL {LOOKBACK_S} SECOND
GROUP BY sid"""

    if rung_id == "R05_group_by_gauge":
        # PromQL: sum by (service) ({g}{cluster="..."})
        return f"""
SELECT svc, sum(v) AS total
FROM (
  SELECT cityHash64(Attributes) AS sid,
         any(Attributes['service']) AS svc,
         argMax(Value, TimeUnix) AS v
  FROM {GAUGE_T}
  WHERE MetricName = '{g}'
    AND Attributes['cluster'] = '{p["cluster"]}'
    AND TimeUnix >= now() - INTERVAL {LOOKBACK_S} SECOND
  GROUP BY sid
)
GROUP BY svc"""

    if rung_id == "R06_rate_group_by":
        # PromQL: sum by (service) (rate({c}{cluster="..."}[5m]))
        sub = _rate_subquery(SUM_T, c, f"AND Attributes['cluster'] = '{p['cluster']}'", 300)
        return f"""
SELECT svc, sum(r) AS total
FROM ({sub})
GROUP BY svc"""

    if rung_id == "R07_topk":
        # PromQL: topk(10, sum by (pod) (rate({c}{cluster="..."}[5m])))
        sub = _rate_subquery(SUM_T, c, f"AND Attributes['cluster'] = '{p['cluster']}'", 300)
        return f"""
SELECT pod, sum(r) AS total
FROM ({sub})
GROUP BY pod
ORDER BY total DESC
LIMIT 10"""

    if rung_id == "R08_histogram_quantile":
        # PromQL: histogram_quantile(0.99,
        #           sum by (le, service) (rate({h}_bucket{cluster="..."}[5m])))
        #
        # Buckets are CUMULATIVE. After fanning in by (service, le), sort by
        # le, take the +Inf bucket as the total, find where the cumulative count
        # crosses 0.99*total and linearly interpolate inside that bucket -
        # which is what histogram_quantile does.
        return f"""
WITH per_series AS (
  SELECT
    sid,
    any(svc) AS svc, any(le) AS le,
    sumIf(d, d > 0) / 300 AS r
  FROM (
    SELECT
      cityHash64(Attributes) AS sid,
      Attributes['service'] AS svc,
      Attributes['le'] AS le,
      Value - lagInFrame(toNullable(Value), 1, NULL) OVER (
        PARTITION BY cityHash64(Attributes) ORDER BY TimeUnix
        ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
      ) AS d
    FROM {GAUGE_T}
    WHERE MetricName = '{h}_bucket'
      AND Attributes['cluster'] = '{p["cluster"]}'
      AND TimeUnix >= now() - INTERVAL 300 SECOND
  )
  GROUP BY sid
),
by_le AS (
  SELECT svc,
         if(le = '+Inf', inf, toFloat64OrNull(le)) AS lef,
         sum(r) AS cum
  FROM per_series
  GROUP BY svc, lef
),
shaped AS (
  SELECT svc, arraySort(x -> x.1, groupArray((lef, cum))) AS pts
  FROM by_le
  GROUP BY svc
)
SELECT svc, p99 FROM (
SELECT
  svc,
  arrayReduce('max', arrayMap(x -> x.2, pts)) AS total,
  0.99 * total AS target,
  arrayFirstIndex(x -> x.2 >= target, pts) AS idx,
  if(idx = 0, NULL,
    if(isInfinite(pts[idx].1),
       -- PromQL returns the highest FINITE bound when the crossing lands in +Inf
       arrayReduce('max', arrayFilter(v -> NOT isInfinite(v), arrayMap(x -> x.1, pts))),
       -- linear interpolation inside the crossing bucket
       (if(idx = 1, 0.0, pts[idx - 1].1))
       + (pts[idx].1 - if(idx = 1, 0.0, pts[idx - 1].1))
         * (target - if(idx = 1, 0.0, pts[idx - 1].2))
         / nullIf(pts[idx].2 - if(idx = 1, 0.0, pts[idx - 1].2), 0)
    )
  ) AS p99
FROM shaped
)"""

    if rung_id == "R09_join_group_left":
        # PromQL: sum by (node) (
        #           sum by (cluster,namespace,pod) (rate({c}{cluster="..."}[5m]))
        #           * on (cluster,namespace,pod) group_left (node)
        #           topk by (cluster,namespace,pod) (1,
        #             max by (cluster,namespace,pod,node) ({i}{node!=""})))
        #
        # The topk-by-1 guard in the PromQL picks ONE node per pod so the join
        # stays many-to-one. argMax on the info side does the same job.
        sub = _rate_subquery(SUM_T, c, f"AND Attributes['cluster'] = '{p['cluster']}'", 300)
        return f"""
SELECT info.node AS node, sum(a.r) AS total
FROM (
  SELECT cl, ns, pod, sum(r) AS r
  FROM ({sub})
  GROUP BY cl, ns, pod
) AS a
INNER JOIN (
  SELECT
    Attributes['cluster']   AS cl,
    Attributes['namespace'] AS ns,
    Attributes['pod']       AS pod,
    argMax(Attributes['node'], TimeUnix) AS node
  FROM {GAUGE_T}
  WHERE MetricName = '{i}'
    AND Attributes['node'] != ''
    AND TimeUnix >= now() - INTERVAL {LOOKBACK_S} SECOND
  GROUP BY cl, ns, pod
) AS info
ON a.cl = info.cl AND a.ns = info.ns AND a.pod = info.pod
GROUP BY node"""

    if rung_id == "R10_burn_rate":
        # PromQL: the SRE Workbook multi-window multi-burn-rate alert.
        #   (err/total[5m]  > 14.4*0.001 AND err/total[1h] > 14.4*0.001)
        #   OR
        #   (err/total[30m] > 6*0.001    AND err/total[6h] > 6*0.001)
        # Eight rate pipelines, four divisions, two ANDs, one OR union.
        cl = f"AND Attributes['cluster'] = '{p['cluster']}'" if "cluster" in p else ""
        err = "AND match(Attributes['status_code'], '^5..$')"

        def ratio(window_s, alias):
            e = _rate_subquery(SUM_T, c, f"{err}", window_s)
            t = _rate_subquery(SUM_T, c, "", window_s)
            return f"""
{alias} AS (
  SELECT t.svc AS svc,
         sum(e.r) / nullIf(sum(t.r), 0) AS ratio
  FROM ({t}) AS t
  LEFT JOIN ({e}) AS e ON e.sid = t.sid
  GROUP BY t.svc
)"""

        return f"""
WITH
{ratio(300,   'w5m').strip()},
{ratio(3600,  'w1h').strip()},
{ratio(1800,  'w30m').strip()},
{ratio(21600, 'w6h').strip()}
SELECT svc
FROM (
  SELECT w5m.svc AS svc,
         (w5m.ratio  > 14.4 * 0.001 AND w1h.ratio > 14.4 * 0.001) AS fast,
         (w30m.ratio >  6.0 * 0.001 AND w6h.ratio >  6.0 * 0.001) AS slow
  FROM w5m
  LEFT JOIN w1h  ON w1h.svc  = w5m.svc
  LEFT JOIN w30m ON w30m.svc = w5m.svc
  LEFT JOIN w6h  ON w6h.svc  = w5m.svc
)
WHERE fast OR slow"""

    raise KeyError(f"no ClickHouse translation for {rung_id}")
