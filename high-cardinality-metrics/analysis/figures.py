#!/usr/bin/env python3
"""figures.py: print every figure on the results page from results/page-data.json.

  python3 analysis/figures.py results/page-data.json [--summary-concurrency 50]

Engine time: Coralogix p50 minus coralogixFloorMs, the fastest any Coralogix
query completed in the run (build_page_data.py computes and stores it), never
below 0.01 ms; self-hosted p50 unchanged. A cell where every request failed reads "stopped".

Figures:
  1. Summary: every query at 1M series (b4) and one concurrency level, engine
     time, with the winner (fastest platform with no errors) marked *.
  2. Error grid: per platform, band and concurrency (10, 30, 50, 100), the error
     rate averaged over the ten queries, each query weighted equally (a cell
     where every request failed counts as 100%).
  3. Latency grid: the same cells, the median of the ten query medians
     (engine time, successful requests only).
  4. Concurrency: rate by service (R06) at 1M series, p50 from 10 to 100 at once.
  5. Cardinality: rate by service (R06) at 30 at once, p50 for each band.
  6. All results: p50 for every query, platform and band at 10, 30, 50 and 100.
"""
import argparse, json

GRID_LEVELS = [10, 30, 50, 100]
HERO = "R06_rate_group_by"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--summary-concurrency", type=int, default=50)
    a = ap.parse_args()
    D = json.load(open(a.data))
    floor = D["coralogixFloorMs"]
    E = [e["key"] for e in D["engines"]]
    Q = [q["key"] for q in D["queries"]]
    B = [b["key"] for b in D["bands"]]

    def raw(e, q, b, c):
        return D["cells"].get(f"{e}|{q}|{b}|{c}")

    def val(e, d):
        if not d or d.get("allFail") or d.get("p50") is None:
            return None
        return max(d["p50"] - floor, 0.01) if e == "coralogix" else d["p50"]

    def winner(q, b, c):
        best, bv = None, None
        for e in E:
            d = raw(e, q, b, c)
            v = val(e, d)
            if v is not None and not d.get("fail") and (bv is None or v < bv):
                best, bv = e, v
        return best

    def fmt(e, d):
        if not d:
            return "n/a"
        if d.get("allFail"):
            return "stopped"
        s = f"{val(e, d):.1f}"
        if d.get("fail"):
            s += f" ({d['failPct']}% err)"
        return s

    def table(title, rows, cols):
        print(f"\n== {title}")
        w = max(len(str(r[0])) for r in rows + [cols])
        print("  " + str(cols[0]).ljust(w) + "  " + "  ".join(f"{c:>22}" for c in cols[1:]))
        for r in rows:
            print("  " + str(r[0]).ljust(w) + "  " + "  ".join(f"{x:>22}" for x in r[1:]))

    src = D.get("coralogixFloorFrom") or {}
    print(f"Coralogix network floor: {floor} ms" + (
        f" (fastest successful Coralogix request: {src.get('rung')}, {src.get('band')}, "
        f"concurrency {src.get('concurrency')})" if src else ""))

    c0 = a.summary_concurrency
    rows = []
    for q in Q:
        w = winner(q, "b4", c0)
        rows.append([q] + [fmt(e, raw(e, q, "b4", c0)) + ("*" if e == w else "") for e in E])
    table(f"1. Summary, 1M series, {c0} at once, engine time ms (* = winner)", rows, ["query"] + E)

    for e in E:
        rows = []
        for b in reversed(B):
            r = [b]
            for c in GRID_LEVELS:
                rates = []
                for q in Q:
                    d = raw(e, q, b, c)
                    if d:
                        rates.append(100.0 if d.get("allFail") else 100.0 * d.get("fail", 0) / d["n"])
                r.append(f"{sum(rates) / len(rates):.1f}%" if rates else "n/a")
            rows.append(r)
        table(f"2. Error rate, {e} (mean over the ten queries)", rows, ["band"] + GRID_LEVELS)

    for e in E:
        rows = []
        for b in reversed(B):
            r = [b]
            for c in GRID_LEVELS:
                v = sorted(x for x in (val(e, raw(e, q, b, c)) for q in Q) if x is not None)
                r.append(f"{v[len(v) // 2]:.1f}" if v else "stopped")
            rows.append(r)
        table(f"3. Latency, {e} (median of the ten query medians, engine time ms)", rows, ["band"] + GRID_LEVELS)

    rows = [[c] + [fmt(e, raw(e, HERO, "b4", c)) for e in E] for c in D["levels"]]
    table("4. Rate by service at 1M series, engine time ms", rows, ["at once"] + E)

    rows = [[b] + [fmt(e, raw(e, HERO, b, 30)) for e in E] for b in B]
    table("5. Rate by service at 30 at once, engine time ms", rows, ["band"] + E)

    for c in GRID_LEVELS:
        rows = []
        for q in Q:
            for e in E:
                rows.append([f"{q} {e}"] + [fmt(e, raw(e, q, b, c)) + ("*" if winner(q, b, c) == e else "") for b in B])
        table(f"6. All results at {c} at once, engine time ms (* = winner)", rows, ["query platform"] + B)

    print(f"\nwins by platform: {D.get('wins')}")
    print(f"executions: {D.get('totalExecutions'):,}")


if __name__ == "__main__":
    main()
