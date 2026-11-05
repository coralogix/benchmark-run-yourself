#!/usr/bin/env python3
"""
summarise.py: derive tables from the raw JSONL.

  python3 summarise.py <dir-or-file.jsonl> [--phase serial|concurrent]

Cells are grouped by (phase, concurrency, band, rung, backend).
"""
import json, glob, os, statistics as st, collections, argparse

def pct(xs, q):
    if not xs: return None
    xs = sorted(xs)
    return xs[min(int(len(xs) * q), len(xs) - 1)]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--phase", default=None)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.path, "*.jsonl"))) if os.path.isdir(a.path) else [a.path]
    cells = collections.defaultdict(list)
    for f in files:
        for line in open(f):
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if a.phase and r.get("phase") != a.phase: continue
            if r.get("phase") == "verify": continue
            cells[(r["phase"], r.get("concurrency", 1), r["band"], r["rung"], r["backend"])].append(r)

    phases = sorted({(k[0], k[1]) for k in cells})
    for phase, conc in phases:
        print(f"\n{'='*118}\nPHASE: {phase}, concurrency {conc}\n{'='*118}")
        print(f"{'band':5} {'series':>9} {'rung':24} {'backend':10} {'n':>5} "
              f"{'p50':>9} {'p95':>9} {'p99':>9} {'min':>8} {'max':>10} {'err':>5} "
              f"{'series_out':>10} {'eng_p50':>8}")
        for (ph, cc, band, rung, be) in sorted(k for k in cells if k[:2] == (phase, conc)):
            rs = cells[(ph, cc, band, rung, be)]
            lat = [r["latency_ms"] for r in rs]
            ok = [r for r in rs if r.get("ok")]
            errs = len(rs) - len(ok)
            so = [r["result_series"] for r in ok if r.get("result_series") is not None]
            eng = [r["engine_ms"] for r in ok if r.get("engine_ms") is not None]
            print(f"{band:5} {rs[0]['band_series']:>9,} {rung:24} {be:10} {len(rs):>5} "
                  f"{pct(lat,.5):>9.1f} {pct(lat,.95):>9.1f} {pct(lat,.99):>9.1f} "
                  f"{min(lat):>8.1f} {max(lat):>10.1f} {errs:>5} "
                  f"{(int(st.median(so)) if so else 0):>10,} "
                  f"{(pct(eng,.5) if eng else float('nan')):>8.1f}")

    # head-to-head ratio table
    print("\n" + "=" * 118)
    print("HEAD TO HEAD: Coralogix against Mimir, wall p50 and engine-reported p50.")
    print("Mimir shares the client's private network; Coralogix is reached over the")
    print("public internet. The engine columns are each backend's own reported time.")
    print("=" * 118)
    print(f"{'phase':11} {'conc':>4} {'band':5} {'rung':24} {'cx wall':>9} {'mi wall':>9} {'wall x':>7} "
          f"{'cx eng':>8} {'mi eng':>8} {'eng x':>7}")
    for (ph, cc, band, rung, be) in sorted(k for k in cells):
        if be != "coralogix": continue
        cx = [r["latency_ms"] for r in cells[(ph, cc, band, rung, "coralogix")]]
        mi = [r["latency_ms"] for r in cells.get((ph, cc, band, rung, "mimir"), [])]
        if not cx or not mi: continue
        c, m = pct(cx, .5), pct(mi, .5)
        ce = [r["engine_ms"] for r in cells[(ph, cc, band, rung, "coralogix")] if r.get("engine_ms") is not None]
        me = [r["engine_ms"] for r in cells[(ph, cc, band, rung, "mimir")] if r.get("engine_ms") is not None]
        cep, mep = (pct(ce, .5) if ce else None), (pct(me, .5) if me else None)
        exr = (mep / cep) if (cep and mep) else 0
        print(f"{ph:11} {cc:>4} {band:5} {rung:24} {c:>9.1f} {m:>9.1f} {(m/c if c else 0):>7.2f} "
              f"{(cep if cep is not None else float('nan')):>8.1f} "
              f"{(mep if mep is not None else float('nan')):>8.1f} {exr:>7.2f}")

    # errors, verbatim, grouped
    print(f"\n{'='*118}\nERRORS\n{'='*118}")
    seen = collections.Counter()
    samples = {}
    for k, rs in cells.items():
        for r in rs:
            if r.get("ok"): continue
            key = (r["backend"], r["band"], r["rung"], r.get("http_status"),
                   (r.get("error") or r.get("prom_error") or "")[:140])
            seen[key] += 1
            samples.setdefault(key, r)
    if not seen:
        print("  none")
    for (be, band, rung, status, msg), n in seen.most_common(40):
        print(f"  {n:>6} x  {be:10} {band} {rung:24} HTTP {status}  {msg}")

if __name__ == "__main__":
    main()
