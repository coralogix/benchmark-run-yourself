"""The weekly cycle: on the series with a working week, the error of the methods given the 168-hour period divided by
Timestral-123M's.

Methods given the 168-hour period: MSTL (season_length [24, 168]) and same as last week (repeats the last 168 hours). Timestral is given no period.
For each series and horizon: the geometric mean MASE over that task's cutoffs (floored at 0.01, as in the reports),
divided by Timestral-123M's on the same forecasts. Prints every method's ratio, then the range over the declared-cycle
methods. Writes results/production/weekly_cycle.md.

Env: CYCLE_SERIES   comma-separated role names (default message-queue-bytes-in,message-queue-messages-in)
     CYCLE_METHODS  comma-separated method names (default MSTL,seasonal-7d)
"""
import os, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import OUT, results

SERIES = (os.environ.get("CYCLE_SERIES") or "message-queue-bytes-in,message-queue-messages-in").split(",")
DECLARED = (os.environ.get("CYCLE_METHODS") or "MSTL,seasonal-7d").split(",")
REF = "Timestral-123M"
r = results()
out = []
for task in ["24h", "7d"]:
    m = r[(r.task == task) & r.metric.isin(SERIES)].pivot_table(index=["metric", "cutoff"], columns="model", values="mase")
    m = m.dropna().clip(lower=0.01)
    geo = np.exp(np.log(m).groupby(level=0).mean())           # one row per series, one column per method
    ratio = geo.div(geo[REF], axis=0).T.round(2).sort_values(SERIES[0])
    sel = ratio.loc[[c for c in DECLARED if c in ratio.index]]
    out.append(f"## {task}: geometric mean MASE over cutoffs, divided by {REF}'s\n\n{ratio.to_markdown()}\n\n"
               f"Methods told the weekly cycle ({', '.join(sel.index)}): {sel.min().min():.2f} to {sel.max().max():.2f} "
               f"times {REF}'s error.\n")
(OUT / "weekly_cycle.md").write_text("\n".join(out)); print("\n".join(out))
