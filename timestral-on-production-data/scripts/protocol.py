"""The forecast protocol shared by every production script: the window, the series list, the tasks, the series loader
and the loader of the scored results.

Imports only numpy and pandas, so it runs inside every model's own virtualenv.

Environment:
  WINDOW_END    end of the window, 00:00 UTC, exclusive (for example 2026-10-17). Required.
  WINDOW_DAYS   length of the window in days (default 114).
  SERIES_FILE   JSON object of role name to PromQL expression (default config/series.json).
"""
import glob, json, os, pathlib
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "production"
OUT = ROOT / "results" / "production"
SERIES_FILE = pathlib.Path(os.environ.get("SERIES_FILE") or ROOT / "config" / "series.json")


def _end():
    if not os.environ.get("WINDOW_END"):
        raise SystemExit("set WINDOW_END (YYYY-MM-DD, 00:00 UTC, exclusive) in .env")
    return pd.Timestamp(os.environ["WINDOW_END"], tz="UTC")


END = _end()
START = END - pd.Timedelta(days=int(os.environ.get("WINDOW_DAYS") or 114))
GRID = pd.date_range(START, END, freq="1h", inclusive="left")
SERIES = list(json.load(open(SERIES_FILE)))

# Day ahead: 24 steps, a cutoff every day from E-28d to E-1d. Week ahead: 168 steps, cutoffs at E-28, -21, -14, -7 days.
# Horizons within a task never overlap.
TASKS = {"24h": (24, pd.date_range(END - pd.Timedelta(days=28), END - pd.Timedelta(days=1), freq="1D")),
         "7d": (168, pd.date_range(END - pd.Timedelta(days=28), END - pd.Timedelta(days=7), freq="7D"))}


def load(name):
    """One series on the full hourly UTC grid of the window; missing hours stay NaN."""
    d = pd.read_csv(DATA / f"{name}.csv")
    s = pd.Series(d.value.values, index=pd.to_datetime(d.ts, unit="s", utc=True))
    return s.reindex(GRID)


TIMESTRAL = ["Timestral-5M", "Timestral-28M", "Timestral-123M", "Timestral-209M"]
SCORING_RUN = "tsfm_cuda"   # the foundation-model run the accuracy scores come from (GPU)
TIMING_RUN = "tsfm_cpu"     # the separate foundation-model timing run (CPU)


def results():
    """Every scored forecast: naive baselines (naive.csv), classical models (stats_parts/) and the foundation models of
    the scoring run, Timestral included. Timestral appears once, from the scoring run, so the classical and the
    foundation-model comparisons score the same Timestral forecasts."""
    parts = [pd.read_csv(OUT / "naive.csv")] + [pd.read_csv(p) for p in sorted(glob.glob(str(OUT / "stats_parts" / "*.csv")))]
    parts += [pd.read_csv(p) for p in sorted(glob.glob(str(OUT / SCORING_RUN / "*.csv"))) if "_smoke" not in p]
    return pd.concat(parts, ignore_index=True)
