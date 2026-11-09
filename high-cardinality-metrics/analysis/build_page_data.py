#!/usr/bin/env python3
"""build_page_data.py: build the results-page dataset from the raw sweep JSONL.

Reads one directory of harness output (*.jsonl) per platform and writes one
JSON file with p50, p95, p99, sample count and error rate for every
(platform, query, band, concurrency) cell, plus the winner of each cell.

  python3 analysis/build_page_data.py \\
      --engine "coralogix=Coralogix=results/sweep-coralogix" \\
      --engine "prometheus=Prometheus=results/sweep-prometheus" \\
      --engine "victoriametrics=VictoriaMetrics=results/sweep-victoriametrics" \\
      --engine "clickhouse=ClickHouse (MergeTree)=results/sweep-clickhouse" \\
      --engine "clickhouse_timeseries=ClickHouse (TimeSeries)=results/sweep-clickhouse-timeseries" \\
      --engine "mimir=Grafana Mimir=results/sweep-mimir" \\
      --out results/page-data.json

Definitions:
  - Only concurrency 10 to 100 is published; the serial phase (concurrency 1)
    is left out for every platform.
  - Cell latencies are wall-clock times as the harness measured them.
  - Network floor: the fastest any Coralogix query completed in the run, over
    every successful Coralogix request in its directory (any query, band and
    concurrency, the serial phase included). Written to the output as
    coralogixFloorMs, with the request it came from as coralogixFloorFrom.
  - Engine time: Coralogix is reached over the public internet and the
    self-hosted platforms share the client's private network, so engine time
    subtracts the network floor from every Coralogix time. Self-hosted times
    are unchanged.
  - Winner of a cell: the platform with the lowest engine-time p50 among those
    with NO errors in that cell. A platform with errors cannot win.
  - A cell where every request failed is recorded as allFail (stopped
    answering), not as a latency.
"""
import argparse, collections, glob, json, os
import numpy as np

RUNGS = ["R01_pinned", "R02_label_values", "R03_filter_eq", "R04_filter_regex", "R05_group_by_gauge",
         "R06_rate_group_by", "R07_topk", "R08_histogram_quantile", "R09_join_group_left", "R10_burn_rate"]
NICE = {"R01_pinned": "Single series", "R02_label_values": "Label enumeration", "R03_filter_eq": "Label filter",
        "R04_filter_regex": "Regex filter", "R05_group_by_gauge": "Group by service",
        "R06_rate_group_by": "Rate by service", "R07_topk": "Top ten pods", "R08_histogram_quantile": "p99 latency",
        "R09_join_group_left": "Join to metadata", "R10_burn_rate": "Burn-rate alert"}
BANDS = ["b1", "b2", "b3", "b4"]
BL = {"b1": "100k", "b2": "200k", "b3": "500k", "b4": "1M"}
LEVELS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def parse_engine(spec):
    parts = spec.split("=", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"--engine wants key=label=dir, got {spec!r}")
    return tuple(parts)


def pack(e):
    if not e or e["n"] == 0:
        return None
    o = {"n": e["n"], "fail": e["fail"]}
    if e["lat"]:
        a = np.array(e["lat"])
        o["p50"] = round(float(np.percentile(a, 50)), 2)
        o["p95"] = round(float(np.percentile(a, 95)), 2)
        o["p99"] = round(float(np.percentile(a, 99)), 2)
    else:
        o["allFail"] = True
    if e["fail"]:
        o["failPct"] = round(100 * e["fail"] / e["n"], 1)
        o["err"] = e["err"]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="append", type=parse_engine, required=True,
                    help="key=label=dir, once per platform; dir holds that platform's *.jsonl")
    ap.add_argument("--out", default="results/page-data.json")
    a = ap.parse_args()

    cells = collections.defaultdict(lambda: {"lat": [], "fail": 0, "n": 0, "err": None})
    floor, floor_from = None, None
    for key, _, d in a.engine:
        files = glob.glob(os.path.join(d, "*.jsonl"))
        if not files:
            raise SystemExit(f"no *.jsonl under {d} for {key}")
        for f in files:
            for ln in open(f, errors="replace"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if key == "coralogix" and r.get("ok") and r.get("latency_ms") is not None \
                        and (floor is None or r["latency_ms"] < floor):
                    floor = r["latency_ms"]
                    floor_from = {k: r.get(k) for k in ("rung", "band", "concurrency", "phase")}
                c = r.get("concurrency")
                if c not in LEVELS or r.get("rung") not in RUNGS or r.get("band") not in BANDS:
                    continue
                e = cells[(key, r["rung"], r["band"], c)]
                e["n"] += 1
                if r.get("ok"):
                    v = r.get("latency_ms")
                    if v is not None:
                        e["lat"].append(v)
                else:
                    e["fail"] += 1
                    if not e["err"]:
                        e["err"] = " ".join(str(r.get("error") or r.get("prom_error") or "")[:200].split())

    if any(key == "coralogix" for key, _, _ in a.engine) and floor is None:
        raise SystemExit("no successful Coralogix request: the network floor cannot be computed")
    floor = round(floor, 2) if floor is not None else 0.0
    out = {"coralogixFloorMs": floor,
           "coralogixFloorFrom": floor_from,
           "engines": [{"key": k, "label": l} for k, l, _ in a.engine],
           "levels": LEVELS,
           "bands": [{"key": b, "label": BL[b]} for b in BANDS],
           "queries": [{"key": r, "label": NICE[r]} for r in RUNGS],
           "cells": {}}
    for key, _, _ in a.engine:
        for r in RUNGS:
            for b in BANDS:
                for c in LEVELS:
                    p = pack(cells.get((key, r, b, c)))
                    if p:
                        out["cells"][f"{key}|{r}|{b}|{c}"] = p

    def engine_p50(key, p):
        if key == "coralogix":
            return max(p["p50"] - floor, 0.01)
        return p["p50"]

    wins = collections.Counter()
    for r in RUNGS:
        for b in BANDS:
            for c in LEVELS:
                best, bv = None, None
                for key, _, _ in a.engine:
                    p = out["cells"].get(f"{key}|{r}|{b}|{c}")
                    if not p or p.get("allFail") or p["fail"]:
                        continue
                    v = engine_p50(key, p)
                    if bv is None or v < bv:
                        bv, best = v, key
                if best:
                    out["cells"][f"{best}|{r}|{b}|{c}"]["win"] = True
                    wins[best] += 1
    out["wins"] = dict(wins)
    out["totalExecutions"] = sum(v["n"] for v in cells.values())

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    print(f"wrote {a.out}")
    print(f"  executions (concurrency 10 to 100): {out['totalExecutions']:,}")
    print(f"  cells: {len(out['cells']):,}")
    print(f"  Coralogix network floor: {floor} ms ({floor_from})")
    print(f"  wins by platform: {dict(wins)}")


if __name__ == "__main__":
    main()
