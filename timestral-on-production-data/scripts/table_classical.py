"""Viewable table: series, std dev, CV, then mean WAPE (% from true value) per method over the day-ahead forecasts.
The footer counts the series each method wins. Writes results/production/table.html."""
import pathlib, sys, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT as R, TASKS, load, results
r = results()  # Timestral from the foundation-model scoring run, the same forecasts as every other comparison
r = r[r.task == "24h"]
COLS = ["Timestral-5M", "Timestral-28M", "Timestral-123M", "Timestral-209M", "MSTL", "AutoTheta", "CES", "AutoARIMA", "AutoETS",
        "seasonal-24h", "seasonal-7d", "naive-last", "linear-7d"]
LABEL = {"seasonal-24h": "Same as yesterday", "seasonal-7d": "Same as last week", "naive-last": "Last value", "linear-7d": "Straight line (7d)"}
w = r.pivot_table(index="metric", columns="model", values="wape", aggfunc="mean")[COLS]
stats = {n: load(n).dropna() for n in w.index}
cv = pd.Series({n: s.std() / abs(s.mean()) for n, s in stats.items()}).sort_values()
def human(v):
    for u, k in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= k: return f"{v/k:,.2f}{u}"
    return f"{v:,.3g}"
rows = []
for n in cv.index:
    best = w.loc[n].min()
    cells = "".join(f'<td class="num{" best" if w.loc[n, c] == best else ""}">{w.loc[n, c]:.1f}%</td>' for c in COLS)
    rows.append(f'<tr><td><code>{n}</code></td><td class="num">{human(stats[n].std())}</td><td class="num">{cv[n]*100:.0f}%</td>{cells}</tr>')
wins = w.idxmin(axis=1).value_counts()
foot = "".join(f'<td class="num">{wins.get(c, 0)}</td>' for c in COLS)
head = "".join(f"<th>{LABEL.get(c, c)}</th>" for c in COLS)
cut = TASKS["24h"][1]
html = f"""<!doctype html><meta charset="utf-8"><title>Timestral on production metrics</title>
<style>
body{{font:14px/1.45 -apple-system,Inter,sans-serif;background:#fcfcfb;color:#0b0b0b;margin:40px}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:#52514e;margin:0 0 20px;max-width:1000px}}
table{{border-collapse:collapse}} th,td{{padding:7px 10px;border-bottom:1px solid #e6e5e1}}
th{{text-align:right;font-weight:600;font-size:12px;color:#52514e;vertical-align:bottom}} th:first-child{{text-align:left}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}} td.best{{background:#e3eefb;font-weight:600}}
.grp{{text-align:center;border-bottom:2px solid #c9c8c3}} tr.foot td{{border-top:2px solid #c9c8c3;font-weight:600}}
</style>
<h1>Production metrics: 24h-ahead forecast accuracy, hourly data</h1>
<p>Each cell is mean WAPE (total absolute error as a % of the total true value) over {len(cut)} non-overlapping daily forecasts,
{cut[0]:%d %b} to {cut[-1]:%d %b %Y}, each using all history from the start of the window up to the forecast start (AutoARIMA: last 4 weeks).
Lower is better; highlighted = most accurate for that series. Rows ordered by std dev / mean, least to most variable.</p>
<table>
<tr><th></th><th></th><th></th><th class="grp" colspan="4">Timestral (Mistral)</th><th class="grp" colspan="5">Classical statistical</th><th class="grp" colspan="4">Simple baselines</th></tr>
<tr><th>Series</th><th>Std dev</th><th>Std dev / mean</th>{head}</tr>
{''.join(rows)}
<tr class="foot"><td>Series won</td><td></td><td></td>{foot}</tr>
</table>"""
(R / "table.html").write_text(html); print(w.loc[cv.index].round(1).to_markdown()); print(wins)
