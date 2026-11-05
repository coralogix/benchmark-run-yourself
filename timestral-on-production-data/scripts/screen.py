"""Characterize each hourly series: missing share, CV (std dev / mean), linear-trend R^2,
daily (lag 24) and weekly (lag 168) autocorrelation, and a daily sparkline.

Usage: screen.py [data folder under the repo, default data/production]
"""
import glob, pathlib, sys, numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from protocol import ROOT, GRID
BARS = " ▁▂▃▄▅▆▇█"
d0 = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "data/production")
def ac(y, k): y = y - np.nanmean(y); a, b = y[k:], y[:-k]; m = ~np.isnan(a) & ~np.isnan(b); return np.corrcoef(a[m], b[m])[0, 1]
for p in sorted(glob.glob(str(d0 / "*.csv"))):
    d = pd.read_csv(p); s = pd.Series(d.value.values, index=pd.to_datetime(d.ts, unit="s", utc=True))
    s = s.reindex(GRID); y = s.to_numpy()
    miss = np.isnan(y).mean(); v = y[~np.isnan(y)]; x = np.arange(len(y))[~np.isnan(y)]
    r2 = np.corrcoef(x, v)[0, 1] ** 2 if v.std() > 0 else 0
    dm = s.resample("1D").mean().to_numpy(); lo, hi = np.nanmin(dm), np.nanmax(dm)
    spark = "".join(" " if np.isnan(z) else BARS[int((z - lo) / ((hi - lo) or 1) * 8)] for z in dm)
    print(f"{pathlib.Path(p).stem:36s} miss={miss:4.0%} cv={v.std()/abs(v.mean() or 1):6.2f} R2={r2:4.2f} ac24={ac(y,24):5.2f} ac168={ac(y,168):5.2f}\n   {spark}")
