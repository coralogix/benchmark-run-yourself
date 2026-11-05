"""Small multiples: the Timestral-123M week-ahead forecast from the last week-ahead cutoff against the actuals.

The forecasts are the scoring run's own (results/production/tsfm_cuda/Timestral-123M_forecasts.npz), not a new run.

Env: CHART_SERIES, comma-separated role names (default: the first nine in the series file).
Writes results/production/forecasts_7d.png.
"""
import os, sys, pathlib, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as mdates
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT, SCORING_RUN, SERIES, TASKS, load
PICK = os.environ["CHART_SERIES"].split(",") if os.environ.get("CHART_SERIES") else SERIES[:9]
H, cutoffs = TASKS["7d"]; C = cutoffs[-1]
F = np.load(OUT / SCORING_RUN / "Timestral-123M_forecasts.npz")  # (h, 9) per forecast: quantiles 0.1 to 0.9
INK, MUTED, ACT, FC = "#0b0b0b", "#52514e", "#2a78d6", "#eb6834"
fig, axes = plt.subplots(3, 3, figsize=(16, 11), facecolor="#fcfcfb")
for ax, n in zip(axes.flat, PICK):
    s = load(n); ctx = s[s.index < C]; act = s[s.index >= C][:H]
    q = F[f"7d|{C:%Y-%m-%d}|{n}"]
    v = ctx.dropna().to_numpy(); scale = np.mean(np.abs(v[24:] - v[:-24])); a = act.to_numpy(); m = ~np.isnan(a)
    mase = np.mean(np.abs(q[m, 4] - a[m])) / scale
    sn = np.mean(np.abs(np.resize(v[-24:], H)[m] - a[m])) / scale
    ax.set_facecolor("#fcfcfb")
    ax.fill_between(act.index, q[:, 0], q[:, 8], color=FC, alpha=0.18, lw=0, label="80% interval")
    ax.plot(ctx.index[-14 * 24:], ctx.values[-14 * 24:], color=MUTED, lw=1, label="context (last 14 days shown)")
    ax.plot(act.index, act.values, color=ACT, lw=1.2, label="actual")
    ax.plot(act.index, q[:, 4], color=FC, lw=1.8, label="Timestral-123M median")
    ax.axvline(C, color=MUTED, lw=0.8, ls=":")
    ax.set_title(f"{n}\nMASE {mase:.2f}  (same-as-yesterday {sn:.2f})", fontsize=10, color=INK, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b")); ax.tick_params(labelsize=8, colors=MUTED)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"): ax.spines[sp].set_color("#d0cfca")
    ax.grid(axis="y", color="#ecebe7", lw=0.6)
h, l = axes.flat[0].get_legend_handles_labels()
fig.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=4, frameon=False, fontsize=10)
fig.suptitle(f"Production metrics: Timestral-123M 7-day forecast from {C:%d %b %Y} 00:00 UTC (hourly)", y=0.995, fontsize=13, color=INK)
fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(OUT / "forecasts_7d.png", dpi=130)
