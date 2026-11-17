# Datasets & Tokenomics

This benchmark measures what curated datasets change for an AI agent that answers questions from telemetry. Three GLM 5.3 agents in opencode answer the same 100 questions about a week of logs and traces from three businesses, ten times each. The first has no datasets, the second is told how to discover them, and the third gets a catalog of every dataset in its system prompt. A grader replies "No." to every wrong answer until the agent is right or gives up, and the scorer compares tokens, time, tool calls and queries.

Results: https://benchmarks.coralogix.com/dataset-agents

## What you need

- A Coralogix account (EU1 matches the published study) with an API key that can run DataPrime queries, manage datasets and run Background Queries, and a Send-Your-Data key. The account needs room for 12 user-defined datasets.
- Your own OpenAI-compatible endpoint that serves GLM 5.3, with a rate limit of at least 18 concurrent agents at the default `PARALLEL=6`.
- Local tools: Python 3.11 or later (standard library only), Rust with cargo, grpcurl, and opencode 1.18.35. `make setup` builds the Coralogix cx CLI at commit `db204ae` (the first with `cx datasets list`) into `.tools/`. Set `CX_CLI_REV` to build another commit.
- A machine that stays on for about 9 days: 7 days of live replay, a few hours of dataset fills, then about 2 days of agent sessions.
- Cost of one run: inference plus Coralogix ingest. The 3,000 sessions use about 1.85 billion GLM 5.3 tokens in total (input plus output, almost all input, including repeated context). The replay sends about 40 million logs and 35 million spans, and the 12 datasets hold about 3 million rows.

## Quick start

```sh
cp .env.example .env    # fill in every value; RUN_START is the first hour of the week you will replay
make setup              # check the tools, send one minute of replay and print how to check it arrived
make replay             # replay the week live from RUN_START (7 days; run it in tmux or with nohup)
make datasets           # create the 12 datasets, fill them from the replayed week, verify them against raw data
make run                # compute the answer key from raw data, then run 3 arms x 100 questions x 10 reps
make score              # compute every published figure into results/study.json
make clean              # stop the agent servers and delete the 12 datasets
```

## Step by step

### 1. Set up the accounts

1. Pick the Coralogix account and region. Create the API key and the Send-Your-Data key.
2. Set up your OpenAI-compatible endpoint for GLM 5.3 and save its API key in a file.
3. Copy `.env.example` to `.env` and fill it in. Set `RUN_START` to an hour in the near future, on the hour, in UTC. Every question names the week that starts there.
4. Run `make setup`. It builds the pinned cx CLI into `.tools/bin`, which every target puts first on `PATH`, checks for `python3`, `cx`, `grpcurl` and `opencode`, checks that `cx datasets list` answers, and sends one minute of replay so you can confirm that logs and spans arrive for `goldspin-play`, `coralogix-store` and `coralogix-finance`.

### 2. Replay the week

The account holds one week of telemetry from three simulated businesses:

- GoldSpin Play (`goldspin-play`): online casino and sports betting. Bets, deposits, withdrawals, jackpots and the odds feed.
- Coralogix Store (`coralogix-store`): an ecommerce merchandise store. Baskets, orders, stock and checkout.
- Coralogix Finance (`coralogix-finance`): a research platform sold to institutional finance clients. Billed queries, inference and market-data costs, refunds and exports.

Each business also runs a customer-facing AI assistant that emits GenAI spans with token counts, costs, tool calls and guardrail results.

`generator/profile.json.gz` is a profile of the week the published study ran on: 168 hourly counts for each of 621 log kinds, exemplar documents for each kind in the exact shape Coralogix stored them, 47 trace root kinds with hourly counts and exemplar trace trees, and pools of real assistant conversations. `generator/generate.py` replays that week hour by hour from `RUN_START`:

- Every log kind is sent in the same number per hour as the profiled week, cloned from its exemplars with fresh timestamps, trace IDs and span IDs. Logs go to the REST logs API (`/logs/v1/singles`) as JSON documents.
- Every trace root kind is sent in the same number per hour, from real traces of that hour and its neighbors, so the hourly latency and error shape follows the profile. Spans go to the OTLP/HTTP traces endpoint (`/v1/traces`).
- Assistant conversations are chosen each hour so that guardrail hits (prompt injection, PII, custom policy), failed tool calls per tool and failed chat calls add up to that hour's counts.

The generator sends in real time and waits when it is ahead of the clock, so `make replay` runs for 7 days. If it stops, restart it with `python3 generator/generate.py run --start $RUN_START --from-hour <next hour>`. Optional settings: `GEN_SEED` (default 7) and `GEN_SCALE` (default 1.0, use a smaller value only for a trial run, since hourly targets round to whole records).

The replay reproduces the business figures of the profiled week closely, but it is a replay, not a copy: amounts are drawn from exemplars and traces are resampled, so exact totals and the slowest single hour can differ from the published week. The answer key in step 4 is computed from your replayed data, so every answer is graded against what is in your account.

### 3. Create and fill the datasets

`datasets/queries.py` defines the 12 datasets: one DataPrime query each, a partition field and the description that `cx datasets list` shows.

| Dataset | One row per |
|---|---|
| `gaming.wallet_events` | GoldSpin money event: bets, deposits, withdrawals, jackpots |
| `gaming.player_daily` | GoldSpin player per UTC day |
| `gaming.odds_feed_hourly` | hour, service and price-feed provider |
| `ecommerce.order_events` | Store commerce event: baskets, orders, order lines, stock-outs |
| `ecommerce.session_funnel` | Store shopping session |
| `fintech.research_billing` | Finance billing event: billed and failed queries, costs, refunds, exports |
| `fintech.client_daily` | Finance client user per UTC day |
| `platform.service_latency_hourly` | hour, app, service, operation and span kind, with latency percentiles |
| `platform.http_requests_hourly` | hour, gateway, route and status code |
| `platform.resource_pressure_hourly` | hour, app and service: connection pools, latency budget breaches, degraded pods |
| `ops.error_rollup_hourly` | hour, app, service, severity and error type |
| `ai.assistant_calls` | GenAI span from the three assistants |

`make datasets` runs `datasets/build.py`:

1. `create` defines the 12 datasets in the `default` dataspace over gRPC (`UserDefinedDatasetService/SetUserDefinedDataset`) with their descriptions.
2. `fill` submits one Background Query per chunk of the replayed week with a persistent destination that appends to the dataset. `gaming.wallet_events`, `ecommerce.order_events`, `platform.service_latency_hourly` and `platform.resource_pressure_hourly` fill in daily chunks, because a Background Query returns at most 1 million rows. The rest fill in one week-long query. The job IDs go to `results/fill_jobs.jsonl`.
3. `status` waits until every fill has finished.
4. `verify` prints the row count of every dataset, and checks that the four event datasets hold exactly as many rows as their raw source query.

Run it after the replay finishes. In the published study the datasets held 1,041,991 wallet events, 1,069,745 order events, 54,870 billing events and 527,137 assistant spans.

### 4. Compute the answer key

`scenarios/questions.json` holds the 100 questions, 25 in each of four tiers:

| Tier | Shape |
|---|---|
| 1 | one filter and one number |
| 2 | a ranking, a top entry or one breakdown |
| 3 | several steps: ratios across event types, rates, per-entity then rank, percentiles |
| 4 | an investigation across services, signals and time, with three or more facts in the answer |

By domain: 22 platform, 20 ecommerce, 20 gaming, 18 fintech, 10 AI assistant and 10 that cross businesses. Datasets make 96 of them easier; 4 need raw data. Questions are written the way a business user, SRE or analyst would ask, with no field names and no DataPrime hints. `{W}` in a question becomes the replayed week (`between <RUN_START> and <RUN_START + 7 days> (end exclusive)`), and the six questions about a single day name a day of that week.

`harness/truth.py` computes the correct answer of every question from raw `logs` and `spans` only, never from a dataset, so the answer key does not depend on the datasets being right. Each question has reference queries and a Python expression that turns their rows into the answer: strings that must appear (with accepted alternatives, such as the ways of writing a date) and numbers with a relative tolerance (0.5% for money and counts, 1% for percentages and averages, wider for percentiles). Questions that ask for a top entry also check that it leads the runner-up by at least 1%. A question that fails this check is marked `NOT_UNIQUE` and left out of the run. The answers go to `results/truth/`.

### 5. Configure the three arms

Every arm runs the same model, opencode version, tools, permissions and questions. The instructions differ only in what they say about datasets (`harness/agents/`).

- No Dataset (`none`): the base instructions: the account, the three businesses, the read-only rule, GBP as the currency, and the answer format (`FINAL ANSWER:` or `ABANDON`). Its opencode permissions deny any command that runs `cx datasets` or names a dataset, so it works from raw `logs` and `spans`.
- Discover Datasets (`discover`): the base instructions plus one paragraph: the account may have curated datasets in the `default` dataspace, `cx datasets list` shows which exist with a description of each, and how to query one.
- Datasets in system prompt (`prompt`): the base instructions plus the full catalog: every dataset, its window, its fields and how to query it.

`harness/arm.sh` gives each arm its own opencode server, home directory and `AGENTS.md`, on ports 47201 to 47203. All three agents can run `cx`, `jq`, `python3` and common text tools. They cannot edit files, fetch web pages, call the APIs directly or read the answer key.

### 6. Run the sessions

`make run` computes the answer key, starts the three servers and runs `harness/run.py`. For each question and repetition the three arms start together:

1. The agent gets the question and works until it replies with a `FINAL ANSWER:` line or `ABANDON`.
2. The grader checks the answer against the answer key. A correct answer ends the session as solved. A wrong one gets "No. That answer is not correct. Keep working and reply with a new FINAL ANSWER line, or reply ABANDON to give up."
3. A reply with neither line gets a nudge to answer or abandon.
4. A session also ends after 8 wrong answers, 3 unanswered nudges, or 45 minutes.

Each session writes `results/runs/<arm>/<question>_r<rep>.json` with the full exchange, every command the agent ran with the start of its output, and the metrics, and appends a line to `results/runs/results.jsonl`. The run is resumable: a session with an outcome is never rerun.

Before each question the runner checks that the model endpoint answers a trivial request within 45 seconds and waits otherwise. A session that hits a model error is recorded as `infra_error` and rerun, and so is a session that averaged more than 60 seconds per model call, because at that pace the endpoint sets the time. GLM 5.3 answers in seconds when healthy.

## How each figure is computed

`make score` runs `analysis/score.py`, which writes `results/study.json` and prints the headline table.

- Unit: a scenario-rep, one question and one repetition with all three arms. A scenario-rep is left out of every figure if any of its arms averaged more than 60 seconds per model call. In the published study 940 of 1,000 scenario-reps remained.
- Tokens: the sum over every model call of input, cached input read and written, output and reasoning tokens, as opencode reports them.
- Time taken: seconds from the question to the end of the session, including every "No." round.
- Tool calls: every tool the agent ran. Queries: commands that ran `cx logs`, `cx spans`, `cx dataprime query`, `cx metrics` or `cx search-fields`.
- Medians are over the scenario-reps of each arm. Percentage changes compare each arm's median with the No Dataset median.
- Ratios compare totals over the same scenario-reps, with 95% intervals from 4,000 bootstrap resamples of the paired scenario-reps. "Discover used 1.63x the tokens of the system-prompt arm" is such a ratio.
- Solved, solved first try (no wrong answer) and timed out are counts of sessions.
- Behaviour, from the commands of the discover and prompt arms: the share of sessions whose first query read a dataset, that used a dataset at all, that went back to raw data after a dataset, that sampled a dataset with `limit` before aggregating, that listed datasets in their first three commands, and whose first dataset was one the question's author listed as useful.
- Tier and domain breakdowns repeat the medians within each group.

The published medians were 797,548 tokens with no datasets, 312,615 when discovering them (61% fewer) and 107,790 with the catalog in the system prompt (86% fewer). Time taken was 599, 347 and 169 seconds, and tool calls 43, 23 and 9.

## Layout

```
generator/   generate.py (the replay) and profile.json.gz (the profiled week)
datasets/    queries.py (the 12 dataset queries and descriptions) and build.py (create, fill, status, verify, delete)
scenarios/   questions.json (the 100 questions, reference queries and answer expressions)
harness/     common.py, truth.py (answer key), arm.sh (one opencode server per arm), run.py (sessions and grading), agents/ (the three AGENTS.md files)
analysis/    score.py (every published figure)
results/     written by the run: fill jobs, answer key, sessions, study.json
```
