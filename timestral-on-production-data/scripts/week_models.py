"""One week, five forecasts: one series from one week-ahead cutoff, each foundation model's forecast against what
happened, with its MASE.

Every forecast is the scoring run's own (results/production/tsfm_cuda/<model>_forecasts.npz) and every score is that
run's row for the same task, cutoff and series, so nothing is forecast again.

Env: WEEK_SERIES  the role name (default cache-memory-used)
     WEEK_CUTOFF  a week-ahead cutoff, YYYY-MM-DD (default the second, WINDOW_END minus 21 days)
     WEEK_MODELS  comma-separated models (default Timestral-123M,t0-gamma,Chronos-2,TiRex-2,TimesFM-2.5)
Writes results/production/week_models.csv (hour, actual, each model's median) and week_models.png.
"""
import os, pathlib, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as mdates
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT, SCORING_RUN, TASKS, load

H, cutoffs = TASKS["7d"]
NAME = os.environ.get("WEEK_SERIES") or "cache-memory-used"
C = pd.Timestamp(os.environ["WEEK_CUTOFF"], tz="UTC") if os.environ.get("WEEK_CUTOFF") else cutoffs[1]
if C not in cutoffs: sys.exit(f"WEEK_CUTOFF must be a week-ahead cutoff: {', '.join(f'{c:%Y-%m-%d}' for c in cutoffs)}")
MODELS = (os.environ.get("WEEK_MODELS") or "Timestral-123M,t0-gamma,Chronos-2,TiRex-2,TimesFM-2.5").split(",")
D = OUT / SCORING_RUN
s = load(NAME); ctx = s[s.index < C]; act = s[s.index >= C][:H]
table = pd.DataFrame({"actual": act.to_numpy()}, index=act.index)
scores = {}
for m in MODELS:
    table[m] = np.load(D / f"{m}_forecasts.npz")[f"7d|{C:%Y-%m-%d}|{NAME}"][:, 4]   # the 0.5 quantile
    row = pd.read_csv(D / f"{m}.csv").query("task == '7d' and metric == @NAME")
    scores[m] = float(row[pd.to_datetime(row.cutoff, utc=True) == C].mase.iloc[0])
table.index.name = "hour"; table.to_csv(OUT / "week_models.csv")
print(f"{NAME}, cutoff {C:%Y-%m-%d}, {H} hours\n" + pd.Series(scores, name="MASE").round(3).to_markdown())

fig, axes = plt.subplots(len(MODELS), 1, figsize=(11, 2.4 * len(MODELS)), sharex=True, facecolor="#fcfcfb")
for ax, m in zip(axes, MODELS):
    ax.plot(ctx.index[-14 * 24:], ctx.values[-14 * 24:], color="#52514e", lw=1, label="history (last 14 days)")
    ax.plot(act.index, act.values, color="#2a78d6", lw=1.2, label="what happened")
    ax.plot(act.index, table[m].values, color="#eb6834", lw=1.8, label="forecast median")
    ax.axvline(C, color="#52514e", lw=0.8, ls=":"); ax.set_title(f"{m}   MASE {scores[m]:.3f}", loc="left", fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
axes[0].legend(loc="upper left", ncol=3, frameon=False, fontsize=8)
fig.suptitle(f"{NAME}: week-ahead forecasts from {C:%d %b %Y} 00:00 UTC", fontsize=12)
fig.tight_layout(); fig.savefig(OUT / "week_models.png", dpi=120)
