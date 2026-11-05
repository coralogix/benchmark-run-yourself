"""Turn a bench.py results folder into WAPE, skill, WQL and coverage tables and a small-multiples chart.

Usage: report.py [results subfolder under results/demo]   (no argument: the full-context run)
Env: DEMO_CUTOFF, DEMO_SERIES_FILE (default config/demo-series.json; rows follow its order).
"""
import json, os, pathlib, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.dates as mdates

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
RES = ROOT / "results" / "demo" / (sys.argv[1] if len(sys.argv) > 1 else "")
ORDER = list(json.load(open(os.environ.get("DEMO_SERIES_FILE") or ROOT / "config" / "demo-series.json")))
BASE = ["naive-last", "seasonal-24h", "linear-24h"]
TS = ["Timestral-5M", "Timestral-28M", "Timestral-123M", "Timestral-209M"]

r = pd.read_csv(RES / "scores.csv")
mae = r.pivot(index="metric", columns="model", values="mae")
best_base = mae[BASE].min(axis=1); best_base_name = mae[BASE].idxmin(axis=1)
skill = (1 - mae[TS].div(best_base, axis=0)) * 100   # % MAE reduction vs best naive baseline; >0 = model wins
skill["best baseline"] = best_base_name
skill = skill.loc[[m for m in ORDER if m in skill.index]]
wape = r.pivot(index="metric", columns="model", values="wape").loc[skill.index, TS + BASE]
print("WAPE % (lower is better)\n", wape.round(2).to_markdown(), "\n")
print("Skill vs best baseline (% MAE reduction, higher is better)\n", skill.round(1).to_markdown(), "\n")
t = r[r.model.isin(TS)].pivot(index="metric", columns="model", values="wql").loc[skill.index, TS]
print("WQL % (probabilistic, lower is better)\n", t.round(2).to_markdown(), "\n")
c = r[r.model.isin(TS)].pivot(index="metric", columns="model", values="cov80").loc[skill.index, TS]
print("80% interval coverage (target 80)\n", c.round(1).to_markdown())
skill.to_csv(RES / "skill.csv")

# --- chart: one panel per metric, last 24h of context + the forecast day ---
S = pd.read_csv(RES / "series.csv", parse_dates=["ts"]); F = pd.read_csv(RES / "forecasts.csv", parse_dates=["ts"])
MODEL = "Timestral-123M"
INK, MUTED, ACT, FC = "#0b0b0b", "#52514e", "#2a78d6", "#eb6834"
fig, axes = plt.subplots(3, 3, figsize=(16, 11), facecolor="#fcfcfb")
for ax, m in zip(axes.flat, skill.index):
    s = S[S.metric == m]; ctx = s[s.part == "context"].tail(288); act = s[s.part == "actual"]; f = F[(F.metric == m) & (F.model == MODEL)]
    ax.set_facecolor("#fcfcfb")
    ax.fill_between(f.ts, f.q10, f.q90, color=FC, alpha=0.18, linewidth=0, label="80% interval")
    ax.plot(ctx.ts, ctx.value, color=MUTED, lw=1.2, label="context (input)")
    ax.plot(act.ts, act.value, color=ACT, lw=1.2, label="actual")
    ax.plot(f.ts, f["median"], color=FC, lw=2, label=f"{MODEL} median")
    ax.axvline(pd.Timestamp(os.environ["DEMO_CUTOFF"]), color=MUTED, lw=0.8, ls=":")
    sk = skill.loc[m, MODEL]; wp = wape.loc[m, MODEL]
    ax.set_title(f"{m}\nWAPE {wp:.1f}%  ·  skill vs best baseline {sk:+.0f}%", fontsize=10, color=INK, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %H:%M")); ax.tick_params(labelsize=8, colors=MUTED)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"): ax.spines[sp].set_color("#d0cfca")
    ax.grid(axis="y", color="#ecebe7", lw=0.6)
h, l = axes.flat[0].get_legend_handles_labels()
fig.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=4, frameon=False, fontsize=10)
fig.suptitle(f"Timestral-123M: forecast for {pd.Timestamp(os.environ['DEMO_CUTOFF']):%d %b %Y} (UTC) from context ending at 00:00, vs actual" + (f"  ({sys.argv[1]})" if len(sys.argv) > 1 else ""), y=0.995, fontsize=13, color=INK)
fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(RES / "forecasts.png", dpi=130)
