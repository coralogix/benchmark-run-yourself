"""One determinism repeat of the post-restart forecast: fresh process, fresh model load, all 4 sizes x 9 series.
Saves the full quantile output so repeats can be compared exactly (compare_repeats.py).

Usage: repeat.py <run index>
Env: DEMO_CUTOFF, DEMO_RESTART (ISO time of the first point after the collector restart).
"""
import os, sys, pathlib, numpy as np
run = sys.argv[1]
sys.argv = [sys.argv[0], os.environ["DEMO_RESTART"], "repeats"]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import bench
from timestral import TimestralForecaster

out = bench.OUT; out.mkdir(parents=True, exist_ok=True)
series = {n: bench.load(n) for n in bench.METRICS}
arrs = {}
for mname in bench.MODELS:
    f = TimestralForecaster.from_pretrained(f"mistralai/{mname}")
    for n, (ctx, act) in series.items():
        arrs[f"{mname}|{n}"] = np.asarray(f.forecast(ctx.to_numpy(dtype=np.float64), prediction_length=bench.H).quantiles)[0, 0]
np.savez(out / f"run{run}.npz", **arrs)
print("run", run, "done")
