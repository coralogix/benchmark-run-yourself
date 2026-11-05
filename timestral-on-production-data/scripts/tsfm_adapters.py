"""One adapter per time-series foundation model. Each returns quantiles of shape (h, 9) at LEVELS.

Every model gets the same raw context: a 1-D float array of all history before the cutoff, leading missing values
trimmed, interior gaps left as NaN. Each model handles the gaps and truncates to its maximum context its own way.
No model is configured: each runs with its library's defaults. The only arguments passed are the horizon, the nine
quantile levels where the library takes them, and, for TimesFM-2.5, the two compile sizes its API requires before
it forecasts at all (max_context 4096, longer than every context here; max_horizon 256, the smallest multiple of its
128-step output patch that covers the 168-step horizon).
"""
import numpy as np
import torch

LEVELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def _trim(ctx):
    ctx = np.asarray(ctx, dtype=np.float32)
    i = np.argmax(~np.isnan(ctx))
    return ctx[i:]


class Timestral:
    def __init__(self, size, device):
        from timestral import TimestralForecaster
        self.f = TimestralForecaster.from_pretrained(f"mistralai/Timestral-{size}", device=device)

    def __call__(self, ctx, h):
        fc = self.f.forecast(_trim(ctx).astype(np.float64), prediction_length=h)
        q = fc.quantiles
        q = (q.float().cpu().numpy() if torch.is_tensor(q) else np.asarray(q))[0, 0]; lv = [round(x, 2) for x in fc.quantile_levels]
        return q[:, [lv.index(l) for l in LEVELS]]


class Chronos2:
    def __init__(self, device):
        from chronos import Chronos2Pipeline
        self.p = Chronos2Pipeline.from_pretrained("amazon/chronos-2", device_map=device)

    def __call__(self, ctx, h):
        q, _ = self.p.predict_quantiles([torch.tensor(_trim(ctx))], prediction_length=h, quantile_levels=LEVELS)
        return q[0][0].float().cpu().numpy()  # (h, 9)


class T0:
    def __init__(self, device):
        from t0 import T0Forecaster
        self.m = T0Forecaster.from_pretrained("theforecastingcompany/t0-gamma").to(device).eval(); self.dev = device

    def __call__(self, ctx, h):
        x = torch.tensor(_trim(ctx))[None].to(self.dev)  # t0 treats NaN as missing natively
        with torch.no_grad():
            out = self.m.predict(x, horizon=h, quantile_levels=LEVELS)
        return out.quantiles[0].float().cpu().numpy()  # (h, 9)


class TiRex2:
    def __init__(self, device):
        from tirex2 import load_model
        self.m = load_model("NX-AI/TiRex-2", device=device)

    def __call__(self, ctx, h):
        from tirex2 import TimeseriesType
        ts = TimeseriesType(target=torch.tensor(_trim(ctx))[None], past_covariates=None, future_covariates=None)
        f = self.m.forecast([ts], prediction_length=h, output_type="numpy")[0]  # (1, 9, h)
        return np.asarray(f)[0].T


class TimesFM25:
    def __init__(self, device):
        import timesfm
        self.m = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
        self.m.compile(timesfm.ForecastConfig(max_context=4096, max_horizon=256))  # every other field at its default

    def __call__(self, ctx, h):
        _, q = self.m.forecast(horizon=h, inputs=[_trim(ctx)])  # (1, h, 10): mean, then 0.1..0.9
        return np.asarray(q)[0, :, 1:10]


def build(name, device):
    if name.startswith("Timestral-"): return Timestral(name.split("-", 1)[1], device)
    return {"Chronos-2": Chronos2, "t0-gamma": T0, "TiRex-2": TiRex2, "TimesFM-2.5": TimesFM25}[name](device)
