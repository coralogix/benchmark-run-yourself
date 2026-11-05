# Agent Intelligence Vendor Comparison

This benchmark measures how well AI agents investigate incidents on five observability platforms: Coralogix, Datadog, Dynatrace, Grafana Cloud and New Relic. The OpenTelemetry Astronomy Shop sends its telemetry to all five at the same list-price spend: Coralogix keeps everything, and each other platform's data is cut by sampling and filters to what that spend buys. Ten scenarios inject a fault (or none), and five agents per platform investigate each one through the platform's own CLI: 250 investigations. A reviewing agent marks every answer against reference measurements from unsampled data, without knowing which platform the agent used.

Results: https://benchmarks.coralogix.com/observability-vendor-comparison

## What you need

- Accounts: one fresh account, project or tenant per platform with nothing else in it, all five active and ingesting for the whole run.
  - Coralogix, with TCO policies routing all data to the Monitoring tier.
  - Datadog, on any site.
  - Dynatrace SaaS, receiving OTLP.
  - Grafana Cloud, one stack.
  - New Relic, US or EU data center.
- An Anthropic API account for Claude Code. Every agent and the reviewer use `claude-opus-5-5` at high effort. Sign Claude Code in (`claude`, then `/login`) or export `ANTHROPIC_API_KEY` before `make run`.
- One host for the shop and the agents, with about 16 GB of memory for Docker. Clone this repository under your home directory: the agents' sandbox denies reads of the home directory, which keeps the harness out of their reach. macOS and Linux both work. On macOS the agents' sandbox uses Seatbelt and the CLIs keep their config under `~/Library/Application Support` and the login keychain; on Linux Claude Code's sandbox needs bubblewrap and socat, and the CLIs keep their config under `~/.config`. Use file-based token storage for any CLI that would otherwise need a desktop keyring on Linux.
- Tools: Docker with Compose v2, git, bash, Python 3.10 or later, Claude Code (`claude`), and the five CLIs: `cx` (Coralogix), `pup` (Datadog), `dtctl` (Dynatrace), `gcx` (Grafana Cloud), `newrelic` (New Relic). Every launch records the version of Claude Code, each CLI, each agent's skills, the shop and every container image in `versions.json` beside the transcripts.
- Cost of one run: about $60 + inference costs. The $60 is the five platforms' ingest at list price for the 11 to 18 hours of `make run` (each platform's pipeline is sized to the same spend). Platform subscriptions, seats and trial terms are not included. The 250 investigations use about 940 million input tokens (about 890 million of them cache reads and 45 million cache writes) and about 13 million output tokens; scoring adds one review of about 20,000 input tokens per investigation.

## Quick start

```sh
cp .env.example .env   # fill in every value
make setup             # fetch and patch the shop, build images, start the stack, build each CLI's skills plugin
make run               # run the ten scenarios, 25 agents each; transcripts and versions go to results/runs/
make score             # blind the transcripts, review them, map marks back, print every figure on the page
make clean             # stop the stack and remove its volumes
```

`make run` takes 11 to 18 hours. Keep the host awake for the whole run, for example with `caffeinate -dimsu make run` on macOS or `systemd-inhibit make run` on Linux. Before `make score`, fill in `results/ground-truth.yaml` (step 11).

## Step by step

1. **Create the five accounts.** Create a fresh account on each platform, with one project or tenant per platform and nothing else in it. Keep all five ingesting for the whole run. Then copy `.env.example` to `.env` and fill it in.
   - Coralogix: route all data to the Monitoring TCO tier with TCO policies. Create a Send-Your-Data API key (`CORALOGIX_PRIVATE_KEY`, with `CORALOGIX_DOMAIN`) and a `cx` profile for the account (`CX_PROFILE`, default `competitor-comparison`).
   - Datadog: create an API key (`DATADOG_API_KEY`), note your site (`DATADOG_SITE`), and log `pup` in to that site.
   - Dynatrace: use a SaaS environment receiving OTLP at `https://<environment-id>.live.dynatrace.com/api/v2/otlp` (`DYNATRACE_OTLP_ENDPOINT`) with an ingest token (`DYNATRACE_API_TOKEN`). Create a `dtctl` context with a read-only token (`DTCTL_CONTEXT`).
   - Grafana Cloud: one stack, its OTLP gateway (`GRAFANA_OTLP_ENDPOINT`), the instance ID (`GRAFANA_INSTANCE_ID`) and an access policy token with write scopes (`GRAFANA_API_TOKEN`). Create a `gcx` context (`GCX_CONTEXT`) and authenticate it with a Viewer service-account token, not OAuth:
     ```sh
     gcx login --token <viewer-service-account-token>
     ```
   - New Relic: the OTLP endpoint for your data center (`NEWRELIC_OTLP_ENDPOINT`) and a license key (`NEWRELIC_LICENSE_KEY`). Create a `newrelic` profile (`NR_PROFILE`) with a user API key and set `NEWRELIC_ACCOUNT_ID`.
   - Check every CLI login:
     ```sh
     agents/preflight.sh
     ```

2. **Run the shop and load.** `scripts/setup-shop.sh` clones the OpenTelemetry Astronomy Shop at commit `dedc017` (release 3.1.0) into `otel-demo/`, applies `shop/patches/`, builds the changed images and the helper image, and starts the stack. Every image is pinned: the shop's own images to the `3.1.0` release tag (`DEMO_VERSION` in `otel-demo/.env.override`), the others to the versions in the shop's `.env`, and the helper's base image by digest. `agents/ops.sh dc` wraps Docker Compose with every env and compose file.
   ```sh
   scripts/setup-shop.sh
   agents/ops.sh dc ps
   ```
   The patches add:
   - A search service (`/api/search` through the frontend proxy) whose match mode is read from a config file on every request: `shop/patches/30-search.patch`.
   - A shop assistant with a product-compare feature, calling an OpenAI-compatible LLM endpoint inside the shop: `shop/patches/40-ai-assistant.patch`. The endpoint is a local stand-in, not a hosted model. It replies in 20 to 50 ms and reports token usage at about 4 characters per token, and when a prompt does not say "JSON only" it wraps the JSON in prose at `LLM_GATEWAY_FREEFORM_RATE` (0.25 in `compose.ai.yaml`) of first replies and 15% of follow-ups.
   - A fraud-detection build with an optional background order reviewer, for the early-warning fault, and a 450 MiB memory limit: `shop/patches/50-fraud-detection.patch`.
   - Load-generator tasks for search and compare, and checkout weights of 2: `shop/patches/20-load-generator.patch`. The load generator runs at 10 users (`LOCUST_USERS=10` in `otel-demo/.env.override`).
   - The collector fan-out to the five platforms, its delivery settings and a 3 GB memory limit for the collector: `shop/patches/10-collector.patch`.

   The other container memory limits are the shop's own: Kafka 620 MiB, astronomy-db 80 MB, email 100 MB. Keep Docker running for the whole run, because a paused Docker stops the telemetry. Stop every other container on the host (list any that restart in `STOP_CONTAINERS`): `docker_stats` reports them to every platform and skews the budget.

3. **Keep the agents blind to the faults.** One OpenTelemetry Collector fans out to five exporters, with a pipeline per platform per signal (`otel-demo/src/otel-collector/otelcol-config-extras.yml`). Every pipeline applies the same filters:
   - Strip feature-flag events and attributes from spans, and drop flagd traces, logs and metrics.
   - Drop log lines where a fault announces itself: anything matching "feature flag" or "overload simulation", and the garbage-collection and lock-contention announcements.
   - Drop the collector's own metrics and the logs of load-generator, otelcol-contrib and kafka.
   - Exclude the fault-injection helper images (`harness-*`) from `docker_stats`, and enable `docker_stats`' container CPU limit and throttling series.
   - Compute request, error and latency metrics from all traffic before sampling: the span-metrics connector, or for Datadog its own APM-stats connector.

4. **Make the collector deliver everything it keeps.** Drops come only from the sampling and cuts in step 6. Every exporter has the same delivery settings: a queue of 100,000 items (spans, log records or metric points) with 10 senders, batches of 1,000 to 2,000 items, retries for up to 10 minutes and a 30 s timeout. Each tail sampler buffers up to 200,000 traces for 30 s before deciding. After every scenario, `agents/delivery-check.py` reads the collector's own counters over the agents' window and fails the run if any exporter failed to send or queue data, any receiver refused data (the memory limiter refuses at the receiver) or any sampler decided a trace early; `agents/run-all.sh` then runs the scenario again at the end of the queue. Check any window by hand:
   ```sh
   agents/delivery-check.py <start-utc> <end-utc>
   ```
   If a check fails, raise `queue_size` (and the collector's memory limit in `compose.extras.yaml`) for every exporter alike, restart the collector and run the scenario again.

5. **Price the telemetry.** Price the shop's telemetry at each platform's public list price with 15-day retention, in each platform's own billing unit, and write the results into `config/budget.json`.
   - Measure the shop's telemetry with nothing cut over six clean hours: log records and bytes, spans and bytes per span, active metric series and datapoints, and the mix by volume across traces, metrics and logs.
   - Convert GiB to GB, and turn per-event charges into per-GB with the measured log and span sizes. For each platform and signal, the affordable share is the volume of Coralogix's data the same money buys at that platform's price. Take flat fees out of the $10,000 monthly reference budget.
   - List prices: Coralogix logs $0.42/GB, traces $0.16/GB, metrics $0.06/GB, plus $0.0115/GB for the customer's own S3 storage. Datadog $0.10/GB ingest plus $2.55 per million indexed log events or spans (15-day retention), including applicable APM allowances; custom metrics $5 per 100 indexed and $0.10 per 100 ingested a month; APM host fee $48/month. Dynatrace logs and traces $0.20/GiB ingest plus $0.0007/GiB-day retention; metrics $0.15 per 100,000 datapoints, counted from Dynatrace's own billing events; a modeled 10% query reserve. Grafana Cloud logs and traces $0.40/GB write, $0.05/GB processing, $0.10/GB retention at 30 days prorated to 15; metrics $6.50 per 1,000 active series a month; $19/month platform fee. New Relic $0.40/GB for all data, plus $0.05/GB in the EU data center; three Full Platform seats at $208/month.
   - In `config/budget.json`, `split` is Coralogix's spend split across traces (T), logs (L) and metrics (M) for the measured mix; `table` is each other platform's affordable share per signal (filled with the results page's values); `dt_billing_factor` is Dynatrace's billed datapoints divided by the datapoints the collector sends to it, read from Dynatrace's billing usage events over a clean window.

6. **Cut each platform to budget.** Each other platform's pipeline has its own sampling and cuts in `otelcol-config-extras.yml`. Start from these settings and adjust them until each lands within about 5% of its budget in a no-fault window. Slow means a whole trace over 1.5 s.
   - Coralogix: everything, with no sampling and no metric or log cuts.
   - Dynatrace: tail sampling keeps all error traces, all slow traces and 38% of the rest. Drop generic runtime and client-library metrics (`filter/cut_cardinality`: datadog.trace_agent, otel.sdk, http.client, rpc.client, process.runtime, nodejs.eventloop, dotnet.gc, v8js, jvm, go.memory, db.sql, db.client, dns.lookup). Also drop per-disk system.disk, per-interface system.network (keep system.network.connections), per-core system.cpu.time, container network, container blockio and the container CPU kernel/user/system split (`filter/trim_dynatrace`). Report metrics every 180 s and convert cumulative to delta. Keep all logs.
   - Grafana Cloud: tail sampling keeps errors, slow traces and 32% of the rest. The same runtime metric cut, at full resolution. Drop the logs of recommendation, ad, quote and fraud-detection.
   - New Relic: tail sampling keeps errors, slow traces and 33% of the rest. The same runtime metric cut, and report metrics every 150 s. Drop the same four services' logs.
   - Datadog: tail sampling keeps errors, 45% of slow traces and 11% of the rest. The runtime metric cut plus all system.*, container.* and span-metric series (Datadog gets request metrics from its APM-stats connector). Drop the same four services' logs, and frontend-proxy access logs with a 2xx or 3xx status.

   After a change, restart the collector, wait 30 minutes, and check the window with `agents/budget-window.py`. It reads the collector's exporter counters from the shop's local Prometheus and prices each platform's spans, logs and metrics against `config/budget.json`: Grafana and Datadog metrics as series, New Relic as datapoints, and Dynatrace as datapoints times its billing factor.
   ```sh
   agents/ops.sh dc restart otel-collector
   agents/budget-window.py <start-utc> <end-utc>
   agents/budget-window.py <fault-start-utc> <fault-end-utc> --label=fault
   ```
   Errors and slow traces are always kept, so a fault pushes the sampled platforms over budget. Allow it, and run the script over every fault window to record each platform's share of budget per scenario.

7. **Set up the agents.** Every agent is Claude Code in headless mode: model `claude-opus-5-5`, effort high, tools Bash, Skill, Read and Agent, permission mode `dontAsk`, no MCP servers, no memory, no web (`agents/lib.sh`).
   - Each agent starts from a fresh working directory that contains only a copy of its CLI's skills plugin, under `AGENT_HOME` (replicates 2 to 5 under `AGENT_HOME-r2` to `AGENT_HOME-r5`), with a sandbox that can reach only its own platform's API domains. The sandbox cannot read the home directory (except the CLI's own config), the Docker socket or the harness, and denies process, environment, network and interpreter commands.
   - Each agent gets the skills its own CLI ships and nothing else, loaded as a plugin. `agents/build-cli-plugins.sh` builds one plugin per platform from each CLI's own skills installer (`make setup` runs it) and prints each plugin's skill count and the CLI's version. The New Relic CLI ships none.
     ```sh
     agents/build-cli-plugins.sh
     ```
   - Every platform's agent gets the same system prompt, `agents/system-prompt.md`, with only the CLI, platform, scope and window filled in. The scope names the profile, context, site or account the CLI must use (`platform_config` in `agents/lib.sh`, from `.env`) and nothing else.

8. **Prepare the ten faults.** Each script in `agents/custom/` takes `prep`, `on`, `off` and `status`, and verifies its own change. `agents/run-scenario.sh` runs `prep` before the cool-down, `on` after the baseline and `off` after the agents finish. Four scenarios use demo flags (two directly, plus the Kafka flood and five faults scripts), which `agents/ops.sh flip` switches by rewriting `defaultVariant` in `demo.flagd.json` (same inode) and confirming the served value over OFREP, so flagd never restarts.
   1. Delisted bestseller (lead 15 min): `delist-bestseller.sh` deletes product 9SIQT8TOJO (the bestseller) from the catalog database, and restores it from a backup afterwards.
   2. Recommendation cache (15 min): the demo flag `recommendationCacheFailure`. The runner restarts recommendation afterwards.
   3. Kafka queue flood (15 min): `kafka-flood.sh`, the demo flag `kafkaQueueProblems` (each order published 101 times, fraud-detection 1 s per message). Afterwards it resets fraud-detection's consumer group, and accounting's if its lag is over 500.
   4. CPU throttle (15 min): `cpu-throttle.sh`, `docker update --cpus 0.1` on product-catalog, and recreates the container afterwards.
   5. Search relevance (20 min): `search-relevance.sh` sets search to phrase match, so multi-word searches return nothing.
   6. Ad garbage collection (15 min): the demo flag `adManualGc`. The runner restarts ad afterwards.
   7. Early warning (25 min): `thread-leak.sh`. Fraud-detection's reviewer samples 25% of orders and creates a thread pool per sampled order that is never shut down (about 4 idle threads a minute, container limit 450 MiB).
   8. Optimize (15 min): no fault, `no-fault.sh`.
   9. AI cost (20 min): `ai-retries.sh` switches the compare prompt to a template without the "JSON only" instruction, so the assistant retries silently.
   10. Five faults (25 min): `multi-fault.sh`. paymentFailure 25%, adManualGc, recommendationCacheFailure, cart-to-Valkey latency bursts (250 ms for 60 s every 300 s, from a helper container sharing valkey-cart's network, `latency-bursts.sh`), emailMemoryLeak 100x.

   Try a fault by hand before the run:
   ```sh
   agents/custom/<fault>.sh prep
   agents/custom/<fault>.sh on
   agents/custom/<fault>.sh status
   agents/custom/<fault>.sh off
   agents/ops.sh flip recommendationCacheFailure off on
   agents/ops.sh served recommendationCacheFailure
   agents/ops.sh flip recommendationCacheFailure on off
   agents/ops.sh dc restart recommendation
   ```

9. **Run the scenarios.** `agents/run-all.sh` (`make run`) runs the ten scenarios in the order of its `QUEUE` through `agents/run-scenario.sh`. Each is one cycle: 8 min cool-down, 10 min clean baseline, fault on and verified, the scenario's lead time, the agents, the delivery check, then fault off. The fault stays on while the agents work.
   - quote restarts at the start of each cool-down, because its clock drifts. Before the Kafka flood and the five faults, Kafka and checkout restart outside the window, because Kafka's memory creeps over hours.
   - The agents' window runs from 2 minutes before the baseline to the moment they launch, as fixed UTC times, the same for all five investigations on every platform. Right before every launch, `agents/preflight.sh` runs outside the sandbox: one query per CLI refreshes its token and confirms it points at the right account. A platform that fails preflight three times is left out of that launch.
   - Five agents per platform, 25 per scenario, launch against the same window in shuffled order, 20 s apart, with at most 15 running at once. Agents still running after 70 minutes (90 for five faults) are stopped. Each agent's stream-json transcript and session ID go to `results/runs/<label>-r<N>/`, with `versions.json` (`agents/record-versions.sh`). Ten scenarios give 250 investigations.
   - A run whose fault failed its prep or never verified, with any failed preflight attempt, with agents stopped by the timeout, with a failed delivery check or with a non-zero exit runs again at the end of the queue under the label suffix `R`, for every platform; scoring uses that run.
   ```sh
   caffeinate -dimsu make run       # macOS; on Linux: systemd-inhibit make run
   FROM=3 TO=5 agents/run-all.sh    # entries 3 to 5 only
   DRY=1 agents/run-all.sh          # print the plan
   ```
   To run one scenario on its own:
   ```sh
   QUESTIONS=agents/questions-search.md agents/run-scenario.sh "FS1-searchRelevance|custom|search-relevance.sh|-|20|-"
   ```

10. **Ask the questions.** Each agent gets its questions one at a time in one session, each after the previous answer. `run-agents-rep.sh` reads them from the file named in `QUESTIONS`, separated by lines of `---`.
    - Delisted bestseller, recommendation cache, Kafka queue flood, CPU throttle, ad garbage collection: `agents/questions.md`.
    - Search relevance: `agents/questions-search.md`.
    - Early warning: `agents/questions-earlywarning.md`.
    - Optimize: `agents/questions-optimise.md`.
    - AI cost: `agents/questions-ai-cost.md`.
    - Five faults: `agents/questions-explore.md`.

11. **Measure the reference.** For each scenario, measure the truth from unsampled data: Coralogix's full telemetry inside the agents' window, plus the shop's own database where needed, for example joining order IDs to the accounting table. Record before and during counts, failure rates with their denominators, affected shoppers and orders, and money per currency. For Optimize, list every real improvement the data supports. Copy `scoring/ground-truth-template.yaml` to `results/ground-truth.yaml` and fill it in; write measurements only, without platform names, because the reviewer reads each scenario's section.

12. **Blind the transcripts.** `scoring/blind.py export` writes one Markdown transcript per agent to `results/blind/transcripts/<ID>.md` under a random ID: the questions, the output of every command the agent ran, and its answers. Every platform, product, query language and CLI name, profile, context, site, account, domain and host path is replaced by a neutral label; every command is replaced by `[query N]`; the CLI's own help text and skill files are left out. The ID map (ID to scenario, replicate and platform) goes to `results/blind-key.csv`, outside the blind directory. Extend the redaction with `scoring/redact-terms.txt` (one `regex<TAB>label` per line) if a transcript still names a platform.
    ```sh
    python3 scoring/blind.py export results/runs results/blind results/blind-key.csv
    grep -il -E 'coralogix|datadog|dynatrace|grafana|new ?relic' results/blind/transcripts/*.md
    ```

13. **Review the answers.** `scoring/review.sh` runs the reviewing agent (Claude Code, no tools, no MCP servers, no memory, an empty directory) on each blinded transcript in random order. Its input is `scoring/review-prompt.md`, the scenario's rubric from `scoring/rubric.json`, the scenario's section of `results/ground-truth.yaml` and the transcript (`python3 scoring/blind.py prompt <ID>` prints it). It never sees the ID map. It marks each dimension and lists false claims, as JSON in `results/blind/reviews/<ID>.json`, then `collect` fills `results/blind/marks.csv`. To mark by hand instead, fill in `marks.csv` directly: one row per ID, the dimensions in the order of its `dimensions` column.
    - Marks: 1 correct (the right mechanism, or a supported impact measurement with the correct scope, time window and denominator), 0.5 partial (part right, the mechanism unproven, or an incomplete or approximate estimate), 0 wrong or missed (including an unsupported impact claim). A finding counts only if the agent's own retrieved evidence supports it.
    - Delisted bestseller, Kafka queue flood, search relevance: cause, counts, money volunteered in answer 2, money in answer 3. Out of 20.
    - Recommendation cache, CPU throttle, ad garbage collection, early warning: cause, operational or predicted impact, money in answer 3. Out of 15.
    - AI cost: cause and financial impact, out of 10.
    - Five faults: one cause mark per fault, out of 25, plus the money answer, out of 5: out of 30.
    - Optimize: no cause score. The count of real improvements backed by evidence, and whether the agent found the top item (excessive collector CPU).
    - False claims: statements the reference contradicts, such as inflated failure rates or claims that available telemetry did not exist. Missing a finding is not a false claim. They are counted separately and never subtracted. A platform's query rate limits count as platform behavior.
    ```sh
    scoring/review.sh
    ```

14. **Map the marks back and compute the figures.** `unblind` joins the marks to the ID map and writes `results/scores.csv`; `figures.py` prints every figure on the results page and writes them to `results/figures.json`.
    ```sh
    python3 scoring/blind.py unblind results/blind results/blind-key.csv results/scores.csv
    python3 scoring/figures.py results/scores.csv --json results/figures.json
    ```
    `make score` runs steps 12 to 14.

## How each figure is computed

- **Scoreboard, Cause + impact:** each platform's total per scenario out of its maximum: the sum over its five agents of the counted dimensions in `scoring/rubric.json` (`results/scores.csv` rows with `counts_toward_total` = yes). `scoring/figures.py`.
- **Scoreboard, Cause only:** the cause points alone, out of 5 (five faults: the five causes, out of 25). `scoring/figures.py`.
- **Icons:** in each view, Best (star) for the highest score in the scenario, ties included; otherwise Strong (check) at 80% or more of the maximum, Mixed (dot) from 55% to under 80%, Weak (cross) under 55%. `scoring/figures.py`.
- **Optimize:** the average number of real improvements per agent, Best for the highest; Cause only has no score. `scoring/figures.py`.
- **Finding the cause and measuring impact:** over the nine fault scenarios, weighted equally: cause = mean of cause points over the cause maximum; impact = mean of (total minus cause) over (maximum minus cause maximum); the rank by the mean of total over maximum. `scoring/figures.py`.
- **False claims:** the sum of each platform's false claims over all ten scenarios and its five agents per scenario, fewest first. `scoring/figures.py`.
- **Data affordable at $10,000/month:** per signal, the shares in `config/budget.json` `table` (step 5); blended, 1 / sum over signals of (Coralogix's spend split / share), the share of Coralogix's whole mix that the same spend buys. `scoring/figures.py`.
- **Cost to keep the same data as Coralogix:** $10,000 divided by the blended share, rounded to the nearest $1,000. `scoring/figures.py`.
- **Data retained at each budget:** the collector settings per platform (`otel-demo/src/otel-collector/otelcol-config-extras.yml`, step 6), and the spans each platform received as a share of Coralogix's in normal running: the `spans%` column of `agents/budget-window.py` over a no-fault window.
- **Spend during incidents:** each platform's share of budget per scenario: the `spend % of budget` column of `agents/budget-window.py` over each fault window.

## Layout

```
README.md, Makefile, .env.example
shop/patches/         changes to the OpenTelemetry Astronomy Shop: collector fan-out, cuts and delivery
                      settings, pinned image tag, load generator, search service, AI assistant and LLM
                      stand-in, fraud-detection
scripts/setup-shop.sh fetches the shop at a pinned commit into otel-demo/, applies the patches, builds, starts
otel-demo/            the patched shop, created by make setup; not committed
agents/               the harness
  lib.sh, env.sh      agent sandbox, settings and launch (sourced)
  system-prompt.md    the system prompt every agent gets
  questions*.md       the questions per scenario
  run-all.sh          the ten scenarios in order, with one retry for a failed run
  run-scenario.sh     one cycle: cool-down, baseline, fault, agents, delivery check, fault off
  run-agents-rep.sh   launches five agents per platform against one window
  record-versions.sh  writes versions.json for each run
  preflight.sh        refreshes and checks each CLI's login before a launch
  delivery-check.py   fails a window in which the collector lost data it kept
  budget-window.py    each platform's share of budget over a window
  ops.sh              Docker Compose wrapper, flag flips, Kafka checks
  build-cli-plugins.sh  one skills plugin per CLI, from the CLI's own installer
  custom/             the fault scripts and the latency helper's Dockerfile
config/budget.json    pricing inputs for budget-window.py and figures.py
scoring/              rubric.json, ground-truth-template.yaml, blind.py (export, prompt, collect,
                      unblind), review-prompt.md and review.sh (the reviewing agent), figures.py
results/              run output (git-ignored)
```
