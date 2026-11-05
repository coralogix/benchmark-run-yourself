"""Production backtest report: Timestral against the classical and naive methods.
Timestral's rows are the forecasts of the foundation-model scoring run (results/production/tsfm_cuda), the same
forecasts report_tsfm.py scores against the other foundation models. Classical and naive rows: bench_production.py.
MASE floored at 0.01 before geometric means.
95% CIs: cluster bootstrap over series (cutoffs within a series are correlated), 2000 resamples.
Speeds come from the separate timing run on one machine: the classical models from results/production/timing_stats,
Timestral from results/production/tsfm_cpu. Writes results/production/report.md."""
import glob, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT as R, TIMESTRAL as TS, TIMING_RUN, results
r = results()
STATS = ["AutoETS", "AutoARIMA", "AutoTheta", "CES", "MSTL"]
NAIVE = ["seasonal-24h", "seasonal-7d", "naive-last", "linear-7d"]
ALL = TS + STATS + NAIVE
rng = np.random.default_rng(0)
out = []

def geo(x): return float(np.exp(np.log(x).mean()))

for task in ["24h", "7d"]:
    m = r[r.task == task].pivot_table(index=["metric", "cutoff"], columns="model", values="mase")
    m = m[[c for c in ALL if c in m]].dropna().clip(lower=0.01)
    series = m.index.get_level_values(0).unique()
    per_series = {s: m.loc[s] for s in series}
    boot = []
    for _ in range(2000):
        pick = rng.choice(series, len(series))
        boot.append(np.exp(np.log(pd.concat([per_series[s] for s in pick])).mean()))
    boot = pd.DataFrame(boot)
    rank = m.rank(axis=1)
    lb = pd.DataFrame({
        "geomean MASE": np.exp(np.log(m).mean()).round(3),
        "95% CI": [f"{boot[c].quantile(.025):.3f} to {boot[c].quantile(.975):.3f}" for c in m],
        "rel. to seasonal-24h": (np.exp(np.log(m).mean()) / geo(m["seasonal-24h"])).round(3),
        "avg rank": rank.mean().round(2), "#1": (rank == 1).sum()}).sort_values("geomean MASE")
    out.append(f"## Task {task}: {len(m)} forecasts ({len(series)} series x {len(m)//len(series)} cutoffs), {len(m.columns)} methods\n\n{lb.to_markdown()}\n")
    t = "Timestral-123M"
    h2h = []
    for o in [c for c in m if c != t]:
        win = m[t] < m[o]
        bw = [np.mean(np.concatenate([(per_series[s][t] < per_series[s][o]).to_numpy() for s in rng.choice(series, len(series))])) for _ in range(1000)]
        h2h.append({"vs": o, "Timestral-123M wins": f"{win.sum()}/{len(m)} ({win.mean():.0%})", "95% CI": f"{np.quantile(bw,.025):.0%} to {np.quantile(bw,.975):.0%}"})
    out.append(f"### Head to head, Timestral-123M ({task})\n\n{pd.DataFrame(h2h).to_markdown(index=False)}\n")
    ps = np.exp(np.log(m).groupby(level=0).mean())
    st = [c for c in STATS if c in ps]
    best_stat = ps[st].idxmin(axis=1) + " " + ps[st].min(axis=1).round(3).astype(str) if st else "n/a"
    tab = pd.DataFrame({"Timestral-123M": ps[t].round(3), "best classical": best_stat,
                        "seasonal-24h": ps["seasonal-24h"].round(3), "winner": ps.idxmin(axis=1)})
    out.append(f"### Per series, geomean MASE over cutoffs ({task})\n\n{tab.sort_values('Timestral-123M').to_markdown()}\n")
    pr = r[(r.task == task) & r.model.isin(TS)].groupby("model")[["wql9", "cov80"]].mean().round(3)
    out.append(f"### Timestral quantile loss and 80% coverage ({task})\n\n{pr.to_markdown()}\n")
timing = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(str(R / "timing_stats/*.csv")))] +
                   [pd.read_csv(R / TIMING_RUN / f"{m}.csv") for m in TS])
sp = timing[timing.model.isin(TS + STATS)].groupby("model").secs.mean().sort_values().round(3)
out.append(f"## Seconds per series forecast (timing run, one machine)\n\n{sp.to_frame().to_markdown()}\n")
(R / "report.md").write_text("\n".join(out)); print("\n".join(out))
