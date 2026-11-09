#!/usr/bin/env python3
"""
run.py: the query harness. One code path for every backend.

Every execution is recorded as one JSONL line. The raw file is the artefact;
summaries are derived from it.

Every run first samples real label values from each backend to parameterise with.
Phases (--phase):
  verify: run every rung once per backend/band, check it returns data
  serial: N repeats per cell, one query in flight, rotating parameters
  concurrent: C workers sending the same cell back to back
  all: verify, serial, then concurrent

Usage:
  python3 run.py --phase all --repeats 100 --concurrency 10
"""
import argparse, http.client, itertools, json, os, ssl, sys, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from queries import RUNGS, render, metric_names

# The benchmark bands are b1 to b4. b5 and b6 are optional scale-up bands,
# queried only when passed with --bands.
BANDS = ["b1", "b2", "b3", "b4"]
BAND_SERIES = {"b1": 100_000, "b2": 200_000, "b3": 500_000, "b4": 1_000_000,
               "b5": 10_000_000, "b6": 100_000_000}
CTX = ssl.create_default_context()

BACKENDS = {
    "coralogix": dict(base=os.environ.get("CX_PROMQL", ""),
                      key=os.environ.get("CX_API_KEY", "")),
    "mimir":     dict(base=os.environ.get("MIMIR_PROMQL", ""),
                      key=""),
    # One Prometheus node.
    "prometheus": dict(base=os.environ.get("PROM_PROMQL", ""),
                       key=""),
    # One VictoriaMetrics node, Prometheus-compatible query API.
    "victoriametrics": dict(base=os.environ.get("VM_PROMQL", ""),
                            key=""),
    # ClickHouse speaks SQL over its HTTP interface, not PromQL. Queries come
    # from queries_ch.py, which documents each translation.
    "clickhouse": dict(base=os.environ.get("CH_HTTP", ""),
                       key=""),
}

# Mimir runs every component on every node. The harness sends queries round-robin
# over every URL in MIMIR_NODES (comma-separated); empty means MIMIR_PROMQL only.
MIMIR_NODES = [n for n in os.environ.get("MIMIR_NODES", "").split(",") if n]
_rr = itertools.count()

# Fixed query evaluation time (unix seconds), set when the data is historical and
# there is no live ingestion keeping wall-clock now() fresh. Applied as PromQL
# time= and as the now() anchor in ClickHouse SQL. Empty = evaluate at now().
EVAL_TS = os.environ.get("QUERY_EVAL_TIME", "").strip() or None

TIMEOUT = 120.0


# One pooled keep-alive connection per thread per backend, the same for every backend.
_local = threading.local()


def _base_for(backend):
    if backend == "mimir" and MIMIR_NODES:
        return MIMIR_NODES[next(_rr) % len(MIMIR_NODES)]
    return BACKENDS[backend]["base"]


def _conn(backend, base):
    pool = getattr(_local, "pool", None)
    if pool is None:
        pool = _local.pool = {}
    key = (backend, base)
    c = pool.get(key)
    if c is None:
        u = urllib.parse.urlparse(base)
        if u.scheme == "https":
            c = http.client.HTTPSConnection(u.hostname, u.port or 443,
                                            timeout=TIMEOUT, context=CTX)
        else:
            c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=TIMEOUT)
        pool[key] = c
    return c


def _drop(backend, base):
    pool = getattr(_local, "pool", None) or {}
    c = pool.pop((backend, base), None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def http_get(backend, path, params, _retry=True, _base=None):
    cfg = BACKENDS[backend]
    base = _base or _base_for(backend)
    u = urllib.parse.urlparse(base)
    full = u.path + path + "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept-Encoding": "identity",   # measure the real payload, not gzip
        "Connection": "keep-alive",
        # One descriptive User-Agent for every backend.
        "User-Agent": "cx-metric-benchmark/1.0 (+harness)",
    }
    if cfg["key"]:
        headers["Authorization"] = "Bearer " + cfg["key"]

    t0 = time.perf_counter()
    try:
        c = _conn(backend, base)
        c.request("GET", full, headers=headers)
        r = c.getresponse()
        body = r.read()
        dt = (time.perf_counter() - t0) * 1000.0
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        if r.status >= 400:
            return dict(ok=False, ms=dt, status=r.status, bytes=len(body), body=body,
                        headers=hdrs, node=u.hostname, error_class="http_error",
                        error=body.decode("utf-8", "replace")[:2000])
        return dict(ok=True, ms=dt, status=r.status, bytes=len(body), body=body,
                    headers=hdrs, node=u.hostname)
    except Exception as e:
        _drop(backend, base)
        # A pooled connection can be closed by the far end between requests.
        # One re-establish against the same node, then report the error.
        if _retry:
            return http_get(backend, path, params, _retry=False, _base=base)
        return dict(ok=False, ms=(time.perf_counter() - t0) * 1000.0, status=0, bytes=0,
                    body=b"", node=u.hostname, error_class=type(e).__name__,
                    error=str(e)[:2000])


def server_timing_ms(headers):
    """Mimir reports engine time in a Server-Timing header, not in the body.
    `stats=all` returns stats:null on Mimir 2.14, but the header carries
    querier_wall_time in milliseconds. Coralogix does the opposite: body stats,
    no header. The harness reads whichever each one offers, so the engine-time
    column is populated for both."""
    v = (headers or {}).get("server-timing")
    if not v:
        return None
    for part in v.split(","):
        part = part.strip()
        if part.startswith("querier_wall_time"):
            for kv in part.split(";"):
                if kv.strip().startswith("dur="):
                    try:
                        return float(kv.strip()[4:])
                    except Exception:
                        return None
    return None


def parse_result(res):
    """Pull result size and any engine-reported stats out of a Prometheus envelope."""
    out = dict(result_series=None, result_points=None, engine_stats=None, is_partial=None,
               prom_status=None, warnings=None)
    try:
        d = json.loads(res["body"])
    except Exception:
        return out
    out["prom_status"] = d.get("status")
    out["is_partial"] = d.get("isPartial")
    out["warnings"] = d.get("warnings")
    if "stats" in d:
        out["engine_stats"] = d["stats"]
        st = d["stats"] or {}
        # Coralogix: {"executionTimeMsec": 7, "seriesFetched": "2000"}
        # Prometheus/Mimir: {"timings": {"evalTotalTime": 0.0123, ...}, "samples": {...}}
        ms = None
        if "executionTimeMsec" in st:
            try: ms = float(st["executionTimeMsec"])
            except Exception: ms = None
        elif isinstance(st.get("timings"), dict) and "evalTotalTime" in st["timings"]:
            try: ms = float(st["timings"]["evalTotalTime"]) * 1000.0
            except Exception: ms = None
        out["engine_ms"] = ms
        try:
            out["series_fetched"] = int(st.get("seriesFetched")) if st.get("seriesFetched") is not None \
                else (st.get("samples", {}) or {}).get("totalQueryableSamples")
        except Exception:
            out["series_fetched"] = None
    data = d.get("data")
    if isinstance(data, list):                      # label_values
        out["result_series"] = len(data)
    elif isinstance(data, dict):
        r = data.get("result", [])
        out["result_series"] = len(r)
        pts = 0
        for s in r:
            if "values" in s:
                pts += len(s["values"])
            elif "value" in s:
                pts += 1
        out["result_points"] = pts
    if d.get("status") == "error":
        out["prom_error"] = (d.get("error") or "")[:2000]
        out["prom_error_type"] = d.get("errorType")
    return out



# ClickHouse support is imported LAZILY, on first use, and never at module load.
#
# run.py is shared source across backends. An eager `import queries_ch` would
# mean any fault in the ClickHouse translation (a syntax error, a missing file)
# breaks the PromQL backends too. Importing on demand keeps a ClickHouse fault
# contained to the ClickHouse arm.
def _qch():
    global _QCH
    try:
        return _QCH
    except NameError:
        pass
    import queries_ch as _m
    _QCH = _m
    return _QCH


# ---------------------------------------------------------------- ClickHouse
#
# ClickHouse's HTTP interface takes SQL in the POST body. With
# default_format=JSON the response carries a `statistics` block giving the
# server's OWN elapsed time, rows_read and bytes_read: the direct analogue of
# the `stats=all` engine timing read from the Prometheus-compatible engines.

def ch_post(sql, _retry=True):
    base = BACKENDS["clickhouse"]["base"]
    u = urllib.parse.urlparse(base)
    path = (u.path or "/") + "?" + urllib.parse.urlencode({"default_format": "JSON"})
    body = sql.encode("utf-8")
    headers = {
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Content-Type": "text/plain; charset=utf-8",
        "User-Agent": "cx-metric-benchmark/1.0 (+harness)",
    }
    t0 = time.perf_counter()
    try:
        c = _conn("clickhouse", base)
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        payload = r.read()
        dt = (time.perf_counter() - t0) * 1000.0
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        if r.status >= 400:
            return dict(ok=False, ms=dt, status=r.status, bytes=len(payload), body=payload,
                        headers=hdrs, node=u.hostname, error_class="http_error",
                        error=payload.decode("utf-8", "replace")[:2000])
        return dict(ok=True, ms=dt, status=r.status, bytes=len(payload), body=payload,
                    headers=hdrs, node=u.hostname)
    except Exception as e:
        _drop("clickhouse", base)
        if _retry:
            return ch_post(sql, _retry=False)
        return dict(ok=False, ms=(time.perf_counter() - t0) * 1000.0, status=0, bytes=0,
                    body=b"", headers={}, node=u.hostname,
                    error_class=type(e).__name__, error=str(e)[:2000])


def parse_ch(res):
    out = {}
    try:
        d = json.loads(res["body"])
    except Exception:
        if res["ok"]:
            out["prom_error"] = "unparseable response"
        return out
    rows = d.get("data")
    if isinstance(rows, list):
        out["result_series"] = len(rows)
        out["result_points"] = len(rows)
    st = d.get("statistics") or {}
    if "elapsed" in st:
        out["engine_ms"] = round(float(st["elapsed"]) * 1000.0, 3)
    for k_src, k_dst in (("rows_read", "ch_rows_read"), ("bytes_read", "ch_bytes_read")):
        if k_src in st:
            out[k_dst] = st[k_src]
    out["prom_status"] = "success"
    return out


def sample_params_ch(band):
    """Real label values from ClickHouse, as lists in the same form sample_params()
    returns, so pick() rotates them the same way."""
    n = _qch().metric_names_ch(band)
    c = n["c"]
    out = {}

    def distinct(label, limit=200):
        res = ch_post("SELECT DISTINCT Attributes['%s'] AS v FROM %s "
                      "WHERE MetricName = '%s' ORDER BY v LIMIT %d"
                      % (label, _qch().SUM_T, c, limit))
        try:
            return [list(r.values())[0] for r in json.loads(res["body"])["data"]]
        except Exception:
            return []

    for lbl in ["cluster", "namespace", "service", "region", "http_route",
                "http_method", "status_code", "customer_id", "pod"]:
        out[lbl] = distinct(lbl)

    # Real label sets for the pinned rung, as on the PromQL path.
    keys = ["cluster", "namespace", "service", "pod", "region",
            "http_route", "http_method", "status_code", "customer_id"]
    cols = ", ".join("Attributes['%s'] AS %s" % (k, k) for k in keys)
    res = ch_post("SELECT %s FROM %s WHERE MetricName = '%s' LIMIT 50"
                  % (cols, _qch().SUM_T, c))
    try:
        out["_series"] = json.loads(res["body"])["data"]
    except Exception:
        out["_series"] = []
    return out


def execute(backend, band, rung, params, phase, repeat, run_id, worker=0):
    if backend == "clickhouse":
        sql = _qch().build(rung["id"], band, params, eval_ts=EVAL_TS)
        res = ch_post(sql)
        parsed = parse_ch(res)
        rec = dict(
            run_id=run_id, phase=phase, backend=backend, band=band,
            band_series=BAND_SERIES[band], rung=rung["id"], repeat=repeat, worker=worker,
            ts=time.time(), latency_ms=round(res["ms"], 3), http_status=res["status"],
            ok=res["ok"], response_bytes=res["bytes"], node=res.get("node"),
            query=sql, params=params,
            **{k: v for k, v in parsed.items() if k != "body"},
        )
        if not res["ok"]:
            rec["ok"] = False
            rec["error_class"] = res.get("error_class")
            rec["error"] = res.get("error")
        return rec
    r = render(rung, backend, band, params)
    if r["kind"] == "label_values":
        path = f"/label/{r['label']}/values"
        q = {"match[]": r["match"], **label_window(backend)}
    else:
        path = "/query"
        # stats=all makes Prometheus-compatible engines report their OWN
        # evaluation time. Coralogix returns stats unconditionally; Mimir needs
        # the parameter. Engine-reported time is recorded beside wall time
        # because the self-hosted backends share the client's private network
        # and Coralogix is reached over the public internet.
        q = {"query": r["q"], "stats": "all"}
        # Pin the instant-query evaluation time inside the backfilled data window.
        # Without live ingestion the data does not reach wall-clock now(), so an
        # unpinned query evaluates on an empty recent window and returns nothing.
        if EVAL_TS:
            q["time"] = EVAL_TS
    res = http_get(backend, path, q)
    parsed = parse_result(res)
    if parsed.get("engine_ms") is None:
        parsed["engine_ms"] = server_timing_ms(res.get("headers"))
    rec = dict(
        run_id=run_id, phase=phase, backend=backend, band=band,
        band_series=BAND_SERIES[band], rung=rung["id"], repeat=repeat, worker=worker,
        ts=time.time(), latency_ms=round(res["ms"], 3), http_status=res["status"],
        ok=res["ok"] and parsed.get("prom_status") == "success",
        response_bytes=res["bytes"], node=res.get("node"),
        query=r.get("q") or r.get("match"),
        params=params, **{k: v for k, v in parsed.items() if k != "body"},
    )
    if not res["ok"]:
        rec["error_class"] = res.get("error_class")
        rec["error"] = res.get("error")
    return rec


def label_window(backend):
    """VictoriaMetrics' label and series APIs search only the current day unless given
    a range; Prometheus and the others search all their data. Give VictoriaMetrics the
    week before the query instant, so every engine searches the same week."""
    if backend != "victoriametrics" or not EVAL_TS:
        return {}
    end = int(float(EVAL_TS))
    return {"start": str(end - 168 * 3600), "end": str(end)}


def sample_params(backend, band):
    """Pull real label values so queries are parameterised from the data, not guessed."""
    if backend == "clickhouse":
        return sample_params_ch(band)
    names = metric_names(backend, band)
    def vals(label, match):
        res = http_get(backend, f"/label/{label}/values", {"match[]": match, **label_window(backend)})
        try:
            return json.loads(res["body"]).get("data", []) or []
        except Exception:
            return []
    c = names["c"]
    out = {}
    for lbl in ["cluster", "namespace", "service", "region", "http_route", "http_method",
                "status_code", "customer_id"]:
        out[lbl] = vals(lbl, c)
    svc = out["service"][0] if out["service"] else "cart-api"
    out["pod"] = vals("pod", f'{c}{{service="{svc}"}}')

    # R01 pins a series that exists: ask the backend for real label sets;
    # pick() overlays one per repeat.
    res = http_get(backend, "/series", {"match[]": f'{c}{{service="{svc}"}}',
                                        "limit": "500", **label_window(backend)})
    try:
        out["_series"] = json.loads(res["body"]).get("data", []) or []
    except Exception:
        out["_series"] = []
    return out


def pick(pools, i):
    """Rotate parameters across repeats so no two runs hit the same warm entry."""
    def g(k, default):
        v = pools.get(k) or []
        return v[i % len(v)] if v else default
    svc = g("service", "cart-api")
    p = dict(
        cluster=g("cluster", "eu-west-1-prod-a"), namespace=g("namespace", "checkout"),
        service=svc, region=g("region", "eu-west-1"), route=g("http_route", "/v1/checkout"),
        method=g("http_method", "GET"), status=g("status_code", "200"),
        customer=g("customer_id", "cust-0000"), pod=g("pod", "x"),
        servicepfx=svc,
    )
    # Overlay a real, existing label set so the pinned rung returns exactly one
    # series. Rotated across repeats like everything else.
    ss = pools.get("_series") or []
    if ss:
        s0 = ss[i % len(ss)]
        p.update(
            cluster=s0.get("cluster", p["cluster"]), namespace=s0.get("namespace", p["namespace"]),
            service=s0.get("service", p["service"]), region=s0.get("region", p["region"]),
            route=s0.get("http_route", p["route"]), method=s0.get("http_method", p["method"]),
            status=s0.get("status_code", p["status"]), customer=s0.get("customer_id", p["customer"]),
            pod=s0.get("pod", p["pod"]),
        )
        p["servicepfx"] = p["service"]
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "verify", "serial", "concurrent"])
    ap.add_argument("--repeats", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=10)
    # Serial phase: a cell stops once it passes --cell-budget seconds with at
    # least --min-repeats requests.
    ap.add_argument("--cell-budget", type=float, default=150.0,
                    help="max seconds per serial cell; stops early, records actual n")
    ap.add_argument("--min-repeats", type=int, default=8,
                    help="always do at least this many, even if over budget")
    ap.add_argument("--conc-seconds", type=float, default=30.0,
                    help="how long to keep C workers hammering each cell")
    ap.add_argument("--out", default="/opt/bench/results/queries")
    ap.add_argument("--backends", default="coralogix,mimir")
    ap.add_argument("--bands", default=",".join(BANDS))
    ap.add_argument("--rungs", default="")
    args = ap.parse_args()

    backends = args.backends.split(",")
    for b in backends:
        if b not in BACKENDS:
            sys.exit(f"unknown backend {b!r}; known: {', '.join(BACKENDS)}")
        if not BACKENDS[b]["base"] and not (b == "mimir" and MIMIR_NODES):
            env = {"coralogix": "CX_PROMQL", "mimir": "MIMIR_PROMQL",
                   "prometheus": "PROM_PROMQL", "clickhouse": "CH_HTTP",
                   "victoriametrics": "VM_PROMQL"}[b]
            sys.exit(f"{env} is not set; it is required for --backends {b}")
    bands = args.bands.split(",")
    rungs = [r for r in RUNGS if not args.rungs or r["id"] in args.rungs.split(",")]
    run_id = time.strftime("q-%Y%m%dT%H%M%SZ", time.gmtime())
    os.makedirs(args.out, exist_ok=True)
    raw_path = os.path.join(args.out, run_id + ".jsonl")
    raw = open(raw_path, "a", buffering=1)
    def emit(rec): raw.write(json.dumps(rec) + "\n")

    print(f"run {run_id} writes {raw_path}", flush=True)

    pools = {}
    for b in backends:
        for band in bands:
            pools[(b, band)] = sample_params(b, band)
            n = len(pools[(b, band)].get("pod") or [])
            print(f"  sampled {b}/{band}: {n} pods, "
                  f"{len(pools[(b,band)].get('service') or [])} services", flush=True)

    # ---- verify: one shot per cell, prove it returns data before timing anything
    if args.phase in ("all", "verify"):
        print("\n=== VERIFY ===", flush=True)
        for b in backends:
            for band in bands:
                for rung in rungs:
                    rec = execute(b, band, rung, pick(pools[(b, band)], 0), "verify", 0, run_id)
                    emit(rec)
                    flag = "ok " if rec["ok"] else "FAIL"
                    print(f"  {flag} {b:10} {band} {rung['id']:22} "
                          f"{rec['latency_ms']:9.1f}ms series={rec['result_series']} "
                          f"{(rec.get('error') or rec.get('prom_error') or '')[:90]}", flush=True)

    # ---- serial: concurrency 1
    if args.phase in ("all", "serial"):
        print("\n=== SERIAL ===", flush=True)
        for b in backends:
            for band in bands:
                for rung in rungs:
                    lat, errs = [], 0
                    cell_t0 = time.perf_counter()
                    for i in range(args.repeats):
                        rec = execute(b, band, rung, pick(pools[(b, band)], i), "serial", i, run_id)
                        rec["concurrency"] = 1   # serial is the 1-worker point on the sweep
                        emit(rec)
                        lat.append(rec["latency_ms"])
                        if not rec["ok"]:
                            errs += 1
                        # Stop the cell once more than half of at least 10 requests have failed.
                        if len(lat) >= 10 and errs > len(lat) / 2:
                            break
                        if (time.perf_counter() - cell_t0) > args.cell_budget and len(lat) >= args.min_repeats:
                            break
                    lat.sort()
                    p = lambda q: lat[min(int(len(lat) * q), len(lat) - 1)]
                    print(f"  {b:10} {band} {rung['id']:22} n={len(lat)} "
                          f"p50={p(.5):8.1f} p95={p(.95):8.1f} p99={p(.99):8.1f} "
                          f"min={lat[0]:7.1f} max={lat[-1]:9.1f} errs={errs}", flush=True)

    # ---- concurrent: same cells under load
    if args.phase in ("all", "concurrent"):
        print(f"\n=== CONCURRENT (x{args.concurrency}) ===", flush=True)
        for b in backends:
            for band in bands:
                for rung in rungs:
                    lat, errs = [], 0
                    t0 = time.perf_counter()
                    stop_at = t0 + args.conc_seconds
                    counter = [0]
                    lock = threading.Lock()
                    def worker(wid):
                        nonlocal errs
                        while time.perf_counter() < stop_at:
                            with lock:
                                i = counter[0]; counter[0] += 1
                            rec = execute(b, band, rung, pick(pools[(b, band)], i),
                                          "concurrent", i, run_id, wid)
                            rec["concurrency"] = args.concurrency
                            with lock:
                                emit(rec); lat.append(rec["latency_ms"])
                                if not rec["ok"]:
                                    errs += 1
                    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                        for f in as_completed([ex.submit(worker, w) for w in range(args.concurrency)]):
                            f.result()
                    wall = time.perf_counter() - t0
                    lat.sort()
                    p = lambda q: lat[min(int(len(lat) * q), len(lat) - 1)]
                    print(f"  {b:10} {band} {rung['id']:22} n={len(lat)} "
                          f"p50={p(.5):8.1f} p95={p(.95):8.1f} p99={p(.99):8.1f} "
                          f"errs={errs} tput={len(lat)/wall:6.1f}q/s", flush=True)

    raw.close()
    print(f"\nraw: {raw_path}", flush=True)


if __name__ == "__main__":
    main()
