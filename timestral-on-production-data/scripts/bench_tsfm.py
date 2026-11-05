"""Run one foundation model over the production tasks (same cutoffs, horizons, contexts and scoring as bench_production.py).

Usage: bench_tsfm.py <model name> <device> [--smoke]   writes results/production/tsfm_<device>/<model>.csv (scores and
       seconds per forecast) and <model>_forecasts.npz (the nine quantiles of every forecast, keyed "<task>|<cutoff
       date>|<series>").
The GPU run (tsfm_cuda) is the scoring run; the CPU run (tsfm_cpu) is the separate timing run.
Imports no timestral or statsforecast code of its own, so it runs inside each model's own virtualenv.
WQL and coverage use the nine deciles 0.1 to 0.9.
"""
import pathlib, sys, time, numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tsfm_adapters as A
from protocol import OUT as BASE, SERIES, TASKS, load


def score(q, act, scale):
    m = ~np.isnan(act); a = act[m]; qm = q[m]; p = qm[:, 4]
    e = np.abs(p - a); denom = np.abs(a).sum() or np.nan; L = np.array(A.LEVELS)
    d = a[:, None] - qm
    return {"mae": e.mean(), "mase": e.mean() / scale if scale > 0 else np.nan, "wape": e.sum() / denom * 100,
            "wql9": 2 * np.maximum(L * d, (L - 1) * d).mean(1).sum() / denom * 100,
            "cov80": np.mean((a >= qm[:, 0]) & (a <= qm[:, 8])) * 100}


def main():
    name, device = sys.argv[1], sys.argv[2]; smoke = "--smoke" in sys.argv
    out = BASE / f"tsfm_{device}"; out.mkdir(parents=True, exist_ok=True)
    series = {n: load(n) for n in SERIES}
    t0 = time.perf_counter(); model = A.build(name, device); load_s = time.perf_counter() - t0
    rows, fcs = [], {}
    for task, (h, cutoffs) in TASKS.items():
        for c in (cutoffs[-1:] if smoke else cutoffs):
            for n, s in series.items():
                ctx = s[s.index < c]; act = s[s.index >= c][:h].to_numpy()
                v = ctx.dropna().to_numpy(); scale = np.mean(np.abs(v[24:] - v[:-24]))
                t = time.perf_counter(); q = model(ctx.to_numpy(), h); secs = time.perf_counter() - t
                fcs[f"{task}|{c:%Y-%m-%d}|{n}"] = np.asarray(q, np.float32)
                rows.append({"task": task, "cutoff": c, "metric": n, "model": name, **score(np.asarray(q, float), act, scale),
                             "secs": secs, "device": device})
        print(name, task, "done", flush=True)
    df = pd.DataFrame(rows); df["load_s"] = load_s
    tag = '_smoke' if smoke else ''
    df.to_csv(out / f"{name}{tag}.csv", index=False)
    np.savez_compressed(out / f"{name}{tag}_forecasts.npz", **fcs)
    print(name, "rows", len(df), "median secs", round(df.secs.median(), 3), flush=True)


if __name__ == "__main__":
    main()
