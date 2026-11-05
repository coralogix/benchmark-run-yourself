# Timestral on Production Data

This benchmark measures how well Mistral's Timestral forecasting models predict hourly observability series pulled from a Coralogix account, a day and a week ahead, against four other time-series foundation models (t0-gamma, Chronos-2, TiRex-2, TimesFM-2.5) and nine classical and naive methods (MSTL, AutoTheta, CES, AutoARIMA, AutoETS, last value, same as yesterday, same as last week, and a straight line). Every method gets the same history and is scored on the same actuals by MASE, with WAPE, quantile loss and 80% interval coverage beside it. Timestral is run once, and those forecasts are scored against both the foundation models and the classical methods. Speeds come from a separate timing run on one machine. Two side checks on OpenTelemetry demo data measure what a counter reset in the history costs and whether repeated forecasts are bit-identical.

Results: https://benchmarks.coralogix.com/timestral-benchmarks

## What you need

- A Coralogix account whose metrics retention covers a 114-day window ending at 00:00 UTC on a recent date, with metrics that can play the 19 roles in `config/series.example.json`.
- For the side checks: a Coralogix account receiving the OpenTelemetry Astronomy Shop demo for about five days.
- The Coralogix CLI (`cx`), with a profile for each account.
- Python 3.12, `uv` 0.13 or later and `git`. The scripts install torch 2.14.1, Timestral 1.0.0, statsforecast 2.1.1, tfc-t0 0.6.0, chronos-forecasting 2.3.2, tirex-2 0.3.1 and timesfm 3.0.2. The model weights download from Hugging Face on first use.
- AWS CLI v2 with permission to create and delete EC2 instances, security groups and key pairs, plus `ssh`, `rsync` and `make`. The GPU instance launches in the region's default VPC.
- One CPU machine for the naive and classical scoring. A 32 vCPU machine runs every classical cutoff at once.
- One CPU machine for the timing run, used for every model and nothing else while it runs. It takes about ten hours, most of it AutoARIMA. Your own computer will do.
- Cost of one run: about $3, or about $5 with the timing run on a cloud instance. That is about one hour of an AWS g5.2xlarge (one NVIDIA A10G) for the foundation-model scoring run, about one hour of a 32 vCPU on-demand instance for the classical fits, and, for the timing run, about ten hours of a 4 vCPU on-demand instance, at on-demand list prices. Coralogix query costs are not included.

## Quick start

```sh
cp .env.example .env                                  # fill in every value
cp config/series.example.json config/series.json      # then replace every placeholder with your PromQL
cp config/demo-series.example.json config/demo-series.json
make setup    # builds the five model virtualenvs under envs/
make run      # pulls the series, scores the naive and classical methods, runs the GPU scoring run (launch, run, tear down), then the timing run
make demo     # pulls the 5-minute demo series, runs the counter-reset forecasts and the 10 determinism repeats
make score    # writes the leaderboards, head-to-head tables, per-series winners, speed tables and charts under results/
make clean    # deletes any AWS resource tagged PROJECT_TAG, then deletes results/
```

## Step by step

1. **Choose candidate series.** Install `cx` and add your account as a profile. Write one PromQL expression per series, aggregated to a single line, in `config/series.json` (role name to expression). Pick metrics that play the roles in `config/series.example.json` and keep each role's aggregation form: rates as `sum(rate(<counter>[1h]))`, p99 latencies as `histogram_quantile(0.99, sum by (le)(rate(<histogram>_bucket[1h])))`, gauges as `sum(<gauge>)`, counts as `count(<info metric>)`. Aim for a mix: steady, growing, a weekly cycle, spiky and noisy latency. Pull more candidates than the 19 you need.

   ```sh
   cx profiles add <profile> --url <your Coralogix URL>
   cx -p <profile> metrics search --name '<pattern>'
   cx -p <profile> metrics get-labels <metric>
   ```

2. **Fill in the settings.** Copy `.env.example` to `.env`. Set `CX_PROFILE` and `WINDOW_END` (00:00 UTC, exclusive, `YYYY-MM-DD`). Leave `WINDOW_DAYS` empty for 114 days: with `WINDOW_END=2026-10-17` the window is 25 June to 17 October 2026, hourly. Set the AWS variables (`AWS_REGION`, `AWS_PROFILE` if you use one, `GPU_AMI_ID`, `MY_IP`, `PROJECT_TAG`, `KEY_NAME`) for step 9. The other variables have defaults.

3. **Pull the hourly series.** Run `make setup` (step 5) once, then `make pull`. `scripts/fetch.py` runs each expression over the window (2,736 hourly points, end exclusive) in 24-hour chunks, four expressions in parallel. Within a chunk it sums values returned at the same timestamp; where two chunks share an edge timestamp the later chunk overwrites it. It writes `data/production/<role>.csv` with columns `ts` (Unix seconds) and `value`. The scripts load each CSV onto a full hourly UTC grid and leave missing hours as NaN.

   ```sh
   cx -p <profile> -o json metrics query-range '<promql>' --start <ISO start> --end <ISO end> --step 1h   # one chunk
   ```

4. **Screen and select 19 series.** Run `make screen`. It prints each series' missing share, variability (standard deviation over mean), linear-trend R squared, and autocorrelation at lag 24 and lag 168. Exclude any series that is near zero or a single spike in the scoring period. Keep 19 whose standard deviation over mean spreads from about 10% to about 200%, and cut `config/series.json` down to those 19.

5. **Build the model environments.** Run `make setup`. `scripts/setup_envs.sh` gives each model family its own Python 3.12 virtualenv with pandas, numpy and torch 2.14.1, and adds statsforecast, matplotlib and tabulate to the Timestral one. No foundation model is configured: each runs zero-shot with its published weights and its library's defaults, with no fine-tuning and no settings per series or per model (`scripts/tsfm_adapters.py`). The only arguments passed are the horizon and, where the library takes them, the nine quantile levels:
   - Timestral 5M, 28M, 123M, 209M: `mistralai/Timestral-*`, `TimestralForecaster.from_pretrained(...).forecast(context, prediction_length=h)`; it returns 21 quantiles, of which the nine deciles are kept.
   - t0-gamma: `theforecastingcompany/t0-gamma`, `predict(context, horizon=h, quantile_levels=<the nine>)`; it treats NaN as missing.
   - Chronos-2: `amazon/chronos-2`, `Chronos2Pipeline.predict_quantiles(..., prediction_length=h, quantile_levels=<the nine>)`.
   - TiRex-2: `NX-AI/TiRex-2`, `forecast(..., prediction_length=h)`, no covariates.
   - TimesFM-2.5: `google/timesfm-2.5-200m-pytorch`. Its API will not forecast until it is compiled with a maximum context and a maximum horizon, so the adapter passes those two and nothing else: `ForecastConfig(max_context=4096, max_horizon=256)`. 4,096 hours is longer than every context in the window, and 256 is the smallest multiple of its 128-step output patch that covers 168 steps. Every other field keeps the library's default.

6. **Know the inputs each method gets.** The scripts prepare them. Every foundation model, Timestral included, gets the identical raw context: all history before the cutoff as a 1-D float array, leading missing values trimmed, interior gaps left as NaN. Each returns nine quantiles, 0.1 to 0.9, and the 0.5 quantile is its point forecast. The classical methods run in statsforecast (`freq "1h"`, `n_jobs 1`, one series at a time) on the context with gaps filled by linear interpolation in both directions, because statsforecast does not accept missing values: `AutoETS(season_length=24)`, `AutoARIMA(season_length=24)` fitted on the last 672 hours only, `AutoTheta(season_length=24)`, `AutoCES(season_length=24)` (reported as CES), and `MSTL(season_length=[24, 168], trend_forecaster=AutoETS(model="ZZN"))`. The naive baselines use the context forward-filled, then back-filled: last value, same as yesterday, same as last week, and a least-squares line through the last 168 hours.

7. **Know the forecast protocol.** `scripts/protocol.py` defines it. Let E be `WINDOW_END`. Each forecast uses every point before its cutoff as context, 12 to 16 weeks, and is scored on the h points from the cutoff on. Horizons within a task never overlap.
   - Day ahead: 24 hourly steps, 28 cutoffs at 00:00 UTC from E minus 28 days to E minus 1 day: 532 forecasts per method.
   - Week ahead: 168 hourly steps, 4 cutoffs at E minus 28, 21, 14 and 7 days: 76 forecasts per method.

8. **Score the naive and classical methods.** Run `make cpu` on the CPU machine for scoring. To run this step or step 10 on another machine, copy this folder with `.env` and `data/production/` there, run `make setup`, and copy `results/production/` back when it finishes. `scripts/run_cpu.sh naive stats` prints the machine's chip and memory, runs the naive baselines over every cutoff in one process (`bench_production.py naive`, to `results/production/naive.csv`), then runs the classical methods one cutoff per process with `PARALLEL` processes at once (`bench_production.py stats <task> <date>`, to `results/production/stats_parts/`). Timestral is not run here.

9. **Run the scoring run on a GPU.** Run `make gpu`. `infra/launch.sh` creates a key pair and a security group open to `MY_IP` on port 22, and starts one g5.2xlarge tagged `Project=PROJECT_TAG` that powers off and terminates on its own after two hours (`infra/user-data.sh`). `infra/gpu_run.sh` copies the folder and the data up and runs `infra/remote_run.sh` there, which builds the five virtualenvs and runs the eight models (four Timestral sizes and four others) one per process over every cutoff (`scripts/run_tsfm.sh cuda`, which calls `scripts/bench_tsfm.py <model> cuda`). It copies `results/production/tsfm_cuda/` back: per model a CSV of scores and an `_forecasts.npz` of every forecast's nine quantiles. `infra/teardown.sh` then deletes the instance, volumes, security group and key pair and lists anything left. Every foundation-model accuracy figure, Timestral's included, comes from this run, so the classical and the foundation-model comparisons score the same Timestral forecasts.

   ```sh
   infra/launch.sh      # aws ec2 create-key-pair, create-security-group, authorize-security-group-ingress, run-instances
   infra/gpu_run.sh
   infra/teardown.sh    # aws ec2 terminate-instances, delete-volume, delete-security-group, delete-key-pair
   ```

10. **Run the timing run.** Run `make timing` on one CPU machine with nothing else running. `scripts/run_cpu.sh timing` prints the machine's chip and memory, runs the eight foundation models on CPU one after another over every cutoff (`scripts/run_tsfm.sh cpu`, to `results/production/tsfm_cpu/`), then the classical methods one cutoff after another, one process at a time (to `results/production/timing_stats/`). Each forecast call is timed alone with `time.perf_counter`, model loading excluded. This run gives every speed on the page. Its foundation-model scores are the device check: `make score` writes them to `results/production/tsfm_cpu/report.md` beside the GPU run's.

11. **Know how each forecast is scored.** The bench scripts score each forecast over the horizon points whose actual is present and write one row per task, cutoff, series and method:
    - MASE: MAE of the point forecast divided by the mean of |y[t] - y[t-24]| over that cutoff's context, missing points dropped first.
    - WAPE: sum of absolute errors over sum of absolute actuals, times 100.
    - Weighted quantile loss (foundation models): 2 times the mean over the nine quantiles of the pinball loss, summed over time steps, over sum of absolute actuals, times 100.
    - 80% coverage (foundation models): the share of actual points between the 0.1 and 0.9 quantiles, times 100.

12. **Aggregate and draw.** Run `make score`. The reports pivot MASE to one row per series and cutoff and one column per method, drop any row with a method missing, and floor MASE at 0.01. They compute the geometric mean MASE per method, a 95% interval from a cluster bootstrap over series (2,000 resamples, numpy `default_rng(0)`), the average rank (ties averaged), head to head (share of rows where Timestral is strictly lower, ties counted against Timestral, 1,000 resamples), per-series winners, and the ratio of geometric means. `make score` runs, in order:
    - `scripts/report_tsfm.py results/production/tsfm_cuda`: the foundation-model comparison from the scoring run.
    - `scripts/report_tsfm.py results/production/tsfm_cpu`: the same tables from the timing run, for its speed column and the device check.
    - `scripts/report_classical.py`: the classical comparison, with Timestral's rows from the scoring run and speeds from the timing run.
    - `scripts/table_classical.py`: mean WAPE per series and method a day ahead, and the series each method wins.
    - `scripts/chart_week.py`: the week-ahead small multiples from the last week-ahead cutoff, drawn from the scoring run's saved Timestral-123M forecasts. It draws the first nine roles in the series list (`CHART_SERIES` overrides them); the first nine in `config/series.example.json` are the page's nine.
    - `scripts/week_models.py`: one series from one week-ahead cutoff (default `cache-memory-used` from E minus 21 days), each foundation model's saved forecast against what happened, with its MASE.
    - `scripts/weekly_cycle.py`: on the two message-queue throughput series, each method's error divided by Timestral-123M's, and the range over the methods given the 168-hour period.
    - With `results/demo/` present, the demo reports of step 13.

13. **Check resets and determinism.** Send the OpenTelemetry Astronomy Shop demo to a Coralogix account for about five days and restart the collector during the window so the span counter resets. Fill in the `DEMO_*` variables and `config/demo-series.json`, then run `make demo`. It pulls the nine series at a 5-minute step, forecasts the window's last full day (288 steps) from `DEMO_CUTOFF` once with all history (`results/demo/`) and once with history from `DEMO_RESTART` (`results/demo/post-restart/`), then reruns the post-restart forecast for all 4 sizes x 9 series 10 times, each in a fresh process with fresh model loads, saving the full quantile arrays. `make score` prints the WAPE tables and compares the 10 runs element by element.

14. **Compare with the page.** Compare each figure in the next section with the results page.

15. **Clean up.** Run `make clean`. It deletes every AWS resource tagged `PROJECT_TAG` and then `results/`.

## How each figure is computed

- **Foundation-model leaderboards, a day ahead (532 forecasts) and a week ahead (76):** geometric mean MASE, 95% interval and mean 80% coverage over 17 methods, the scoring run merged with the classical and naive results. `scripts/report_tsfm.py results/production/tsfm_cuda`, written to `results/production/tsfm_cuda/report.md`.
- **How much lower Timestral-123M's error is than each other foundation model's:** 1 minus the "MASE ratio (geo)" column of the day-ahead head-to-head table in the same report.
- **Forecasts closer than TimesFM-2.5, and the head-to-head bars against each foundation model:** the day-ahead head-to-head table for Timestral-123M in the same report; the week-ahead range is the same table at 168 steps.
- **Series won a day ahead against foundation models, and the week-ahead split:** the per-series winner table and "Series won" line in the same report.
- **Rivals the 28M outscores:** the number of other foundation models whose geometric mean MASE is above Timestral-28M's in the same report, at both horizons.
- **One week, five forecasts:** each model's 0.5 quantile and MASE for one series and one week-ahead cutoff, from the scoring run's saved forecasts and score rows. `scripts/week_models.py`, written to `results/production/week_models.csv` and `week_models.png`.
- **Week-ahead quantile loss for Chronos-2 and Timestral-209M:** the "WQL (9 deciles)" column of the week-ahead leaderboard in the foundation-model report.
- **Device check (CPU against GPU scores):** `results/production/tsfm_cpu/report.md` against `results/production/tsfm_cuda/report.md`.
- **Classical leaderboards and average ranks:** geometric mean MASE, 95% interval and average rank over 13 methods: the four Timestral sizes from the scoring run, five classical methods and four naive baselines. `scripts/report_classical.py`, written to `results/production/report.md`.
- **Error below MSTL's, a day ahead:** 1 minus Timestral-123M's geometric mean MASE divided by MSTL's, from the day-ahead leaderboard in `results/production/report.md`.
- **Forecasts closer than MSTL, and the head-to-head bars against each classical and naive method:** the day-ahead head-to-head table for Timestral-123M in `results/production/report.md`.
- **Series won a day ahead against classical methods:** lowest error per series over the 28 day-ahead cutoffs among the 13 methods. The footer of `results/production/table.html`, from `scripts/table_classical.py`.
- **The weekly cycle ("further out" on the two message-queue throughput series):** per series and horizon, a method's geometric mean MASE over the cutoffs divided by Timestral-123M's; the range is over the methods given the 168-hour period (`CYCLE_METHODS`, default MSTL and same as last week). `scripts/weekly_cycle.py`, written to `results/production/weekly_cycle.md`.
- **The 5M comparison:** Timestral-123M against Timestral-5M in the day-ahead head-to-head table of `results/production/report.md`.
- **Timestral's coverage range:** the four sizes' mean 80% coverage at both horizons, in the foundation-model report.
- **Time per forecast, foundation models:** the seconds per forecast of the timing run, the "s/forecast" column of `results/production/tsfm_cpu/report.md`, model load excluded.
- **Time per forecast, classical table:** the seconds per forecast of the same timing run, the classical models from `results/production/timing_stats/` and Timestral from `results/production/tsfm_cpu/`: "Seconds per series forecast (timing run, one machine)" in `results/production/report.md`.
- **Timestral against repeating yesterday:** the "rel. to seasonal-24h" column of the day-ahead leaderboard in `results/production/report.md`.
- **The week-ahead forecast chart:** `scripts/chart_week.py`, written to `results/production/forecasts_7d.png`.
- **Counter reset:** Timestral-123M WAPE on `telemetry-spans-exported-total`, with all history against post-restart history. `scripts/demo/bench.py` and `scripts/demo/report.py`.
- **Determinism:** the bit-identical count and the maximum difference over 10 repeats of 36 forecasts. `scripts/demo/repeat.py` and `scripts/demo/compare_repeats.py`.

## Layout

```
Makefile                 help, setup, run, score, clean (and pull, screen, cpu, gpu, timing, demo)
.env.example             every setting you set
config/                  series.example.json (19 production roles), demo-series.example.json (9 demo roles)
scripts/protocol.py      the window, the tasks, the cutoffs, the series loader and the loader of scored results
scripts/cxq.py, fetch.py pull series through the cx CLI
scripts/screen.py        series screening statistics
scripts/bench_production.py  naive and classical forecasts and scores
scripts/tsfm_adapters.py, bench_tsfm.py  one adapter per foundation model; forecasts, scores and timings for one model
scripts/report_*.py, table_classical.py  leaderboards, head to head, per-series winners and speed tables
scripts/chart_week.py, week_models.py, weekly_cycle.py  the week-ahead chart, one week of five forecasts, the weekly cycle
scripts/setup_envs.sh, run_cpu.sh, run_tsfm.sh  environments, the CPU scoring and timing runs, the foundation-model loop
scripts/demo/            counter-reset forecasts, determinism repeats and their comparison
infra/                   launch, run on and tear down the GPU instance
data/                    pulled series (make pull, make demo); not committed
envs/                    model virtualenvs (make setup); not committed
results/                 created by make run, make demo and make score; not committed
```
