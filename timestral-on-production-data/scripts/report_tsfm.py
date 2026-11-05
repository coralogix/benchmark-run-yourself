"""Foundation-model comparison on the production tasks.
Usage: report_tsfm.py <foundation-model results folder: results/production/tsfm_cuda (the scoring run) or
       results/production/tsfm_cpu (the timing run, whose scores serve as the device check)>
Merges the foundation-model run, Timestral included, with the classical and naive results of bench_production.py
(same cutoffs, same MASE). The "s/forecast" column is the seconds per forecast of that run.
MASE floored at 0.01 before geometric means; 95% CIs by cluster bootstrap over series (2,000 resamples).
Writes report.md into the results folder."""
import glob, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import ROOT, OUT as R
D = ROOT / sys.argv[1]
fm = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(str(D / "*.csv"))) if "_smoke" not in p])
classical = pd.concat([pd.read_csv(R / "naive.csv")] + [pd.read_csv(p) for p in sorted(glob.glob(str(R / "stats_parts/*.csv")))])
CLASSIC = ["MSTL", "AutoTheta", "CES", "AutoARIMA", "AutoETS", "seasonal-24h", "seasonal-7d", "naive-last", "linear-7d"]
r = pd.concat([fm, classical[classical.model.isin(CLASSIC)]])
FM = sorted(fm.model.unique(), key=lambda m: fm[fm.model == m].mase.median())
rng = np.random.default_rng(0); out = [f"# Foundation models on production metrics ({D.name}, device: {', '.join(fm.device.unique())})\n"]
geo = lambda x: float(np.exp(np.log(x).mean()))

for task in ["24h", "7d"]:
    m = r[r.task == task].pivot_table(index=["metric", "cutoff"], columns="model", values="mase").dropna().clip(lower=0.01)
    series = m.index.get_level_values(0).unique(); per = {s: m.loc[s] for s in series}
    boot = pd.DataFrame([np.exp(np.log(pd.concat([per[s] for s in rng.choice(series, len(series))])).mean()) for _ in range(2000)])
    rank = m.rank(axis=1)
    p = fm[fm.task == task].groupby("model").agg(wql9=("wql9", "mean"), cov80=("cov80", "mean"), secs=("secs", "median"))
    lb = pd.DataFrame({"geomean MASE": np.exp(np.log(m).mean()).round(3),
                       "95% CI": [f"{boot[c].quantile(.025):.3f} to {boot[c].quantile(.975):.3f}" for c in m],
                       "avg rank": rank.mean().round(2), "#1": (rank == 1).sum(),
                       "WQL (9 deciles)": p.wql9.round(3), "80% coverage": p.cov80.round(1),
                       "s/forecast": p.secs.round(3)}).sort_values("geomean MASE")
    out.append(f"## Task {task}: {len(m)} forecasts ({len(series)} series x {len(m)//len(series)} cutoffs), {m.shape[1]} methods\n\n{lb.to_markdown()}\n")
    for t in ["Timestral-123M", "Timestral-209M"]:
        h2h = []
        for o in [c for c in FM if not c.startswith("Timestral")] + ["MSTL"]:
            w = m[t] < m[o]
            bw = [np.mean(np.concatenate([(per[s][t] < per[s][o]).to_numpy() for s in rng.choice(series, len(series))])) for _ in range(1000)]
            h2h.append({"vs": o, f"{t} wins": f"{w.sum()}/{len(m)} ({w.mean():.0%})", "95% CI": f"{np.quantile(bw,.025):.0%} to {np.quantile(bw,.975):.0%}",
                        "MASE ratio (geo)": round(geo(m[t]) / geo(m[o]), 3)})
        out.append(f"### Head to head: {t} ({task})\n\n{pd.DataFrame(h2h).to_markdown(index=False)}\n")
    ps = np.exp(np.log(m).groupby(level=0).mean())
    out.append(f"### Per-series winner ({task}, geomean MASE over cutoffs)\n\n" +
               pd.DataFrame({"winner": ps.idxmin(axis=1), "winner MASE": ps.min(axis=1).round(3),
                             "Timestral-123M": ps["Timestral-123M"].round(3),
                             "best non-Timestral FM": ps[[c for c in FM if not c.startswith("Timestral")]].idxmin(axis=1)}).to_markdown() +
               f"\n\nSeries won: {ps.idxmin(axis=1).value_counts().to_dict()}\n")
(D / "report.md").write_text("\n".join(out)); print("\n".join(out))
