"""Fetch every expression in a series JSON (role name to PromQL) at a fixed step into <FETCH_OUT>/<role>.csv.

Usage: fetch.py <series JSON>
Env: CX_PROFILE, FETCH_START, FETCH_END (ISO, UTC; end exclusive; default the production window in protocol.py),
     FETCH_STEP (default 1h),
     FETCH_CHUNK_H (hours per query, default 24), FETCH_OUT (folder under the repo, default data/production).
"""
import json, os, sys, pathlib, csv, subprocess, datetime as dt
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from cxq import query_range

if os.environ.get("FETCH_START") and os.environ.get("FETCH_END"):
    START, END = os.environ["FETCH_START"], os.environ["FETCH_END"]
else:
    import protocol
    START, END = protocol.START.isoformat(), protocol.END.isoformat()
STEP = (os.environ.get("FETCH_STEP") or "1h"); CHUNK = dt.timedelta(hours=int(os.environ.get("FETCH_CHUNK_H") or 24))
out = pathlib.Path(__file__).resolve().parent.parent / (os.environ.get("FETCH_OUT") or "data/production")
out.mkdir(parents=True, exist_ok=True)
cands = json.load(open(sys.argv[1]))


def one(item):
    name, expr = item
    t0 = dt.datetime.fromisoformat(START.replace("Z", "+00:00")); t1 = dt.datetime.fromisoformat(END.replace("Z", "+00:00"))
    pts = {}
    try:
        while t0 < t1:
            te = min(t0 + CHUNK, t1)
            chunk = {}
            for s in query_range(expr, t0.isoformat(), te.isoformat(), STEP):
                for t, v in s["values"]:
                    chunk[int(t)] = chunk.get(int(t), 0.0) + float(v)  # exprs are aggregated; sum guards stray splits
            pts.update(chunk)  # chunk edges share one step; the later chunk overwrites, never adds
            t0 = te
    except subprocess.CalledProcessError as e:
        return name, "ERROR " + e.stderr.strip().splitlines()[-1][:160]
    with open(out / f"{name}.csv", "w") as f:
        w = csv.writer(f); w.writerow(["ts", "value"])
        for t in sorted(pts): w.writerow([t, pts[t]])
    return name, len(pts)


with ThreadPoolExecutor(4) as ex:
    for name, n in ex.map(one, cands.items()): print(f"{name:36s} {n}", flush=True)
