"""Timestral single-cutoff forecast on 5-minute OpenTelemetry demo metrics (counter-reset check).

Context: every 5m point in data/demo up to DEMO_CUTOFF (00:00 UTC on the window's last full day).
Horizon: the next 24h (288 steps), scored against what the metric did.
Each Timestral size is compared with three naive baselines so the error numbers have a reference.

Usage: bench.py [<ISO context start> <results subfolder>]
  With no arguments the context is all history and results go to results/demo/.
  With a start time the context begins there (the first point after the collector restart).
Env: DEMO_CUTOFF (required), DEMO_SERIES_FILE (default config/demo-series.json).
"""
import json, os, pathlib, sys, time
import numpy as np, pandas as pd
from timestral import TimestralForecaster

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CUTOFF = pd.Timestamp(os.environ["DEMO_CUTOFF"])  # ISO with Z, for example 2026-10-16T00:00:00Z
H = 288  # 24h at 5m
CTX_START = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else None
OUT = ROOT / "results" / "demo" / (sys.argv[2] if len(sys.argv) > 2 else "")
METRICS = list(json.load(open(os.environ.get("DEMO_SERIES_FILE") or ROOT / "config" / "demo-series.json")))
MODELS = ["Timestral-5M", "Timestral-28M", "Timestral-123M", "Timestral-209M"]


def load(name):
    d = pd.read_csv(ROOT / "data" / "demo" / f"{name}.csv")
    s = pd.Series(d.value.values, index=pd.to_datetime(d.ts, unit="s", utc=True))
    grid = pd.date_range(s.index[0], CUTOFF + pd.Timedelta(minutes=5 * (H - 1)), freq="5min")
    s = s.reindex(grid)  # gaps become NaN; Timestral treats NaN as missing
    ctx, act = s[s.index < CUTOFF], s[s.index >= CUTOFF]
    if CTX_START is not None:
        ctx = ctx[ctx.index >= CTX_START]
    return ctx.loc[ctx.first_valid_index():], act


def baselines(ctx):
    c = ctx.ffill().to_numpy()
    out = {"naive-last": np.full(H, c[-1]),
           "seasonal-24h": c[-H:] if len(c) >= H else np.full(H, np.nan)}
    y = c[-H:]; x = np.arange(len(y))
    k = np.polyfit(x, y, 1)
    out["linear-24h"] = np.polyval(k, np.arange(len(y), len(y) + H))
    return out


def scores(pred, act, q=None, levels=None):
    m = ~np.isnan(act); a, p = act[m], pred[m]
    e = np.abs(p - a); denom = np.sum(np.abs(a)) or 1
    r = {"mae": e.mean(), "wape": e.sum() / denom * 100,
         "smape": np.mean(2 * e / (np.abs(a) + np.abs(p) + 1e-12)) * 100}
    if q is not None:
        qm = q[m]; lv = np.array(levels)
        diff = a[:, None] - qm
        pin = np.maximum(lv * diff, (lv - 1) * diff)  # pinball loss per quantile
        r["wql"] = 2 * pin.mean(1).sum() / denom * 100  # GIFT-Eval style mean weighted quantile loss
        lo, hi = qm[:, levels.index(0.1)], qm[:, levels.index(0.9)]
        r["cov80"] = np.mean((a >= lo) & (a <= hi)) * 100
    return r


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    series = {n: load(n) for n in METRICS}
    rows, fc_rows, timing = [], [], []
    for n, (ctx, act) in series.items():
        for b, p in baselines(ctx).items():
            rows.append({"metric": n, "model": b, **scores(p, act.to_numpy())})
            fc_rows += [{"metric": n, "model": b, "ts": t, "median": v} for t, v in zip(act.index, p)]
    for mname in MODELS:
        t0 = time.perf_counter()
        f = TimestralForecaster.from_pretrained(f"mistralai/{mname}")
        load_s = time.perf_counter() - t0
        for n, (ctx, act) in series.items():
            t1 = time.perf_counter()
            fc = f.forecast(ctx.to_numpy(dtype=np.float64), prediction_length=H)
            inf_s = time.perf_counter() - t1
            q = np.asarray(fc.quantiles)[0, 0]  # (H, 21)
            lv = list(fc.quantile_levels); med = q[:, lv.index(0.5)]
            rows.append({"metric": n, "model": mname, **scores(med, act.to_numpy(), q, lv), "infer_s": inf_s})
            timing.append({"model": mname, "metric": n, "context_len": len(ctx), "load_s": load_s, "infer_s": inf_s})
            fc_rows += [{"metric": n, "model": mname, "ts": t, "median": m_, "q10": a_, "q90": b_}
                        for t, m_, a_, b_ in zip(act.index, med, q[:, lv.index(0.1)], q[:, lv.index(0.9)])]
            print(f"{mname:15s} {n:20s} ctx={len(ctx):5d} {inf_s*1000:7.0f} ms  wape={rows[-1]['wape']:.2f}%")
    res = pd.DataFrame(rows); res.to_csv(OUT / "scores.csv", index=False)
    pd.DataFrame(fc_rows).to_csv(OUT / "forecasts.csv", index=False)
    pd.DataFrame(timing).to_csv(OUT / "timing.csv", index=False)
    # actuals + context for plotting
    pd.concat([pd.DataFrame({"metric": n, "ts": s.index, "value": s.values, "part": p}) for n, (c, a) in series.items()
               for p, s in (("context", c), ("actual", a))]).to_csv(OUT / "series.csv", index=False)


if __name__ == "__main__":
    main()
