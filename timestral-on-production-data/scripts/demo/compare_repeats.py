"""Compare every saved determinism repeat element by element against the first.

Prints how many forecasts are bit-identical across all runs and the largest absolute difference found.
Usage: compare_repeats.py   (reads results/demo/repeats/run*.npz)
"""
import glob, pathlib, numpy as np
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
runs = sorted(glob.glob(str(ROOT / "results/demo/repeats/run*.npz")))
if len(runs) < 2: raise SystemExit("need at least two runs in results/demo/repeats/")
z = [np.load(p) for p in runs]; keys = z[0].files
same, maxdiff = 0, 0.0
for k in keys:
    ref = z[0][k]
    diffs = [float(np.nanmax(np.abs(r[k] - ref))) for r in z[1:]]
    identical = all(np.array_equal(r[k], ref, equal_nan=True) for r in z[1:])
    same += identical; maxdiff = max(maxdiff, *diffs)
print(f"{len(runs)} runs, {same}/{len(keys)} forecasts bit-identical across all runs, max difference {maxdiff}")
