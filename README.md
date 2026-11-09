# Run the Coralogix benchmarks yourself

Coralogix is an observability platform for logs, metrics, traces and AI telemetry. This repository contains the code
behind the benchmarks published on https://benchmarks.coralogix.com, so you can run each one on your own accounts and
compute the same figures.

## Benchmarks

| Folder | Results page | What it measures |
|---|---|---|
| [high-cardinality-metrics](high-cardinality-metrics) | [Coralogix Metrics vs Popular Open Source Engines](https://benchmarks.coralogix.com/metrics-concurrency) | PromQL query latency and errors for six metrics backends at matched spend, across cardinality and concurrency. |
| [agents-with-vendors](agents-with-vendors) | [Agent Intelligence Vendor Comparison](https://benchmarks.coralogix.com/observability-vendor-comparison) | How well one coding agent investigates the same incidents through five observability platforms' CLIs. |
| [timestral-on-production-data](timestral-on-production-data) | [Timestral on Production Data](https://benchmarks.coralogix.com/timestral-benchmarks) | Forecast accuracy and speed of Mistral's Timestral models against other forecasting models on production telemetry. |
| [slo-coding-agents](slo-coding-agents) | [SLOs and Coding Agents](https://benchmarks.coralogix.com/slo-coding-agents) | What a coding agent running a shop for three hours does with and without Coralogix SLOs. |

Each folder is self-contained: a README with what you need, the estimated cost of one run, a step by step guide and the
definition of every figure; a Makefile; an `.env.example`; and only the code needed to run it.

## How every folder works

1. Copy `.env.example` to `.env` and fill in your accounts, keys and endpoints. A folder that needs other settings files says so in its README.
2. Run `make setup` to prepare what the benchmark needs.
3. Run `make run` to run the benchmark.
4. Run `make score` to compute the figures. Some folders add a manual step or an extra target before it; the folder's Quick start shows it.
5. Run `make clean` to remove what the run created; the folder's Quick start says what it removes.

`make help` lists the targets.

## Cost

Running a benchmark creates cloud resources and calls paid APIs in your accounts. Read the cost under What you need in a
folder's README before `make setup`, and run `make clean` when you finish.

## Security

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## License

Apache License 2.0. See [LICENSE](LICENSE).
