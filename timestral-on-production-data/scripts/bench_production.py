"""Production backtest on hourly data: naive baselines and classical statsforecast models.

Day ahead: 24 steps, 28 daily cutoffs. Week ahead: 168 steps, 4 weekly cutoffs. Horizons never overlap (protocol.py).
Context is all history before the cutoff. Every method gets the same context and is scored on the same actuals.
Score: MASE (MAE / in-sample same-as-yesterday MAE), and WAPE.
Timestral is not run here: its forecasts come from the foundation-model scoring run (bench_tsfm.py), and the reports
score those same forecasts against these methods.

Usage:
  bench_production.py naive [--smoke]           naive baselines over every cutoff to results/production/naive.csv
  bench_production.py stats <task> <YYYY-MM-DD> classical models for one cutoff (run the cutoffs in parallel)
                                               to results/production/<STATS_OUT>/<task>_<date>.csv
  bench_production.py cutoffs                 print every task and cutoff, one per line (input for the stats runs)
--smoke runs the last cutoff per task only.
Env: STATS_OUT, the folder under results/production for the stats output (default stats_parts; the timing run
     uses timing_stats).
"""
import os, sys, pathlib, time, warnings, numpy as np, pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA, AutoETS, AutoTheta, AutoCES, MSTL
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT, SERIES, TASKS, load
warnings.filterwarnings("ignore")
OUT.mkdir(parents=True, exist_ok=True)

SMOKE = "--smoke" in sys.argv
ARIMA_CTX = 672  # AutoARIMA fits on the last 4 weeks


def naive(ctx, h):
    c = ctx.ffill().bfill().to_numpy()
    y = c[-168:]; k = np.polyfit(np.arange(168), y, 1)
    return {"naive-last": np.full(h, c[-1]),
            "seasonal-24h": np.resize(c[-24:], h),
            "seasonal-7d": c[-168:][:h] if h <= 168 else np.resize(c[-168:], h),
            "linear-7d": np.polyval(k, np.arange(168, 168 + h))}


def stats_models():
    return [AutoETS(season_length=24), AutoARIMA(season_length=24), AutoTheta(season_length=24),
            AutoCES(season_length=24), MSTL(season_length=[24, 168], trend_forecaster=AutoETS(model="ZZN"))]


def score(pred, act, scale):
    m = ~np.isnan(act); a, p = act[m], pred[m]
    if len(a) == 0: return None
    e = np.abs(p - a); denom = np.abs(a).sum() or np.nan
    return {"mae": e.mean(), "mase": e.mean() / scale if scale > 0 else np.nan, "wape": e.sum() / denom * 100}


def run(part, task_sel=None, cutoff_sel=None):
    series = {n: load(n) for n in SERIES}
    rows = []
    for task, (h, cutoffs) in TASKS.items():
        if task_sel and task != task_sel: continue
        for c in cutoffs:
            if cutoff_sel and c != cutoff_sel: continue
            if SMOKE and c != cutoffs[-1]: continue
            ctxs = {n: s[s.index < c] for n, s in series.items()}
            acts = {n: s[s.index >= c][:h].to_numpy() for n, s in series.items()}
            scales = {}
            for n, ctx in ctxs.items():
                v = ctx.dropna().to_numpy(); scales[n] = np.mean(np.abs(v[24:] - v[:-24]))
            def add(n, model, pred, secs=None):
                r = score(pred, acts[n], scales[n])
                if r: rows.append({"task": task, "cutoff": c, "metric": n, "model": model, **r, "secs": secs})
            if part == "naive":
                for n, ctx in ctxs.items():
                    for b, p in naive(ctx, h).items(): add(n, b, p)
            else:
                for n, ctx in ctxs.items():
                    y = ctx.interpolate(limit_direction="both")
                    for sm in stats_models():
                        yy = y.iloc[-ARIMA_CTX:] if isinstance(sm, AutoARIMA) else y
                        df = pd.DataFrame({"unique_id": n, "ds": yy.index.tz_localize(None), "y": yy.to_numpy()})
                        t0 = time.perf_counter()
                        fc = StatsForecast(models=[sm], freq="1h", n_jobs=1).forecast(df=df, h=h)
                        col = [x for x in fc.columns if x not in ("unique_id", "ds")][0]
                        add(n, col, fc[col].to_numpy(), secs=time.perf_counter() - t0)
            print(part, task, c.date(), "done", flush=True)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--smoke"]
    if a and a[0] == "naive":
        run("naive").to_csv(OUT / ("naive_smoke.csv" if SMOKE else "naive.csv"), index=False)
    elif a and a[0] == "stats":
        d = OUT / (os.environ.get("STATS_OUT") or "stats_parts"); d.mkdir(exist_ok=True)
        run("stats", a[1], pd.Timestamp(a[2], tz="UTC")).to_csv(d / f"{a[1]}_{a[2]}.csv", index=False)
    elif a and a[0] == "cutoffs":
        for task, (_, cutoffs) in TASKS.items():
            for c in cutoffs: print(task, c.date())
    else:
        sys.exit(__doc__)
