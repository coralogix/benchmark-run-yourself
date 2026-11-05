# SLOs and Coding Agents

This benchmark measures whether Coralogix SLOs change what a coding agent does when it runs a shop's engineering. A GLM 5.3 Flash agent in opencode works for three hours on Larder, an online grocery shop of 21 services with live customer traffic, a backlog of 100 features and 16 seeded defects. Twenty matched pairs of trials run the same agent, code, data, workload, tools and time, with and without Coralogix SLOs. The evaluator then computes delivery, reliability, revenue, diagnosis and token figures from the raw records of every trial.

Results: https://benchmarks.coralogix.com/slo-coding-agents

## What you need

- An AWS account with permission to run EC2, IAM, SSM Parameter Store, S3 and EventBridge Scheduler, and one region for every trial. The trial instances launch in that region's default VPC.
- Two S3 buckets in that region, for the Coralogix logs and metrics archives. The logs bucket can also store the code bundle and the trial results.
- A Coralogix account (EU1 matches the published study) where you can create teams and API keys.
- Your own OpenAI-compatible endpoint that serves GLM 5.3 Flash, with a rate limit of at least 8 agents x 22 requests per minute (two waves overlap when the second batch starts). The agent and the reviewer both use it.
- Local tools: uv (it installs Python 3.11), the AWS CLI v2, grpcurl, the Coralogix cx CLI (0.1.25 or later), and Node.js 26 with npm for the evaluator check. Each trial instance installs Node.js 26.7.0, uv, jq, opencode 1.18.35 and the cx CLI itself.
- Cost of one run (40 trials): about $60 + inference costs: forty c7i.2xlarge instances in eu-west-1 for about 3.7 hours each. The 40 agents use about 9.7 billion GLM 5.3 Flash tokens in total (input plus output, almost all input, including repeated context; a median of 198 million per trial without SLOs and 262 million with SLOs), plus the reviewer's tokens. The estimate does not include Coralogix ingest for the 40 trials.

## Quick start

```sh
cp .env.example .env    # fill in every value
make setup              # install the harness, mint each Coralogix team's keys, wire the archives, check every team reports READY
make run                # schedule both batches of 10 pairs on EC2 and wait until all 40 trials are fetched into runs/ and results/
make score              # compute every published figure into results/assets/ (the product-page diagnosis needs your manual read first)
make clean              # remove the AWS role, security group, schedules, launch templates and leftover parameters
```

## Step by step

### 1. Set up the accounts

1. Create the AWS account, pick one region and create the logs and metrics buckets. eu-west-1 matches the cost estimate.
2. Set up your OpenAI-compatible endpoint for GLM 5.3 Flash.
3. Copy `.env.example` to `.env` and fill it in. Pin the code you upload: every trial must run the same bundle. Before running any command below by hand, load the settings into your shell: `set -a; . ./.env; set +a`.
5. Install the harness:

```sh
uv sync --frozen
```

### 2. Configure the agent and model

The agent runs opencode 1.18.35 as the `agent` OS user, inside its workspace only. GLM 5.3 Flash serves both the agent and the reviewer.

1. Add a provider for your endpoint to the opencode config at `LARDER_OPENCODE_CONFIG`:

```json
"provider": {"<provider>": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "<your-endpoint>/v1"}, "models": {"glm-5.3-flash": {}}}}
```

2. Put the endpoint's API key in the file at `LARDER_MODEL_KEY_FILE`, and set `LARDER_MODEL=<provider>/glm-5.3-flash` and `LARDER_MODEL_BASE_URL=<your-endpoint>`.
3. `harness/ec2/launch.py` copies the provider config and the API key into SSM. `harness/ec2/bootstrap.sh` checks `<your-endpoint>/v1/models` before the trial and aborts it unless the answer is HTTP 200, then passes the endpoint to the governor as `--llm-upstream`.

What the agent gets:

- Agent command: `opencode run --auto -m <provider>/glm-5.3-flash --format json "Read AGENT_INSTRUCTIONS.md and follow it. Keep working until ./larderctl time shows about 5 minutes remaining, then write REPORT.md and stop."`
- LLM governor (`harness/runtime/llm_governor.py`): 22 requests per minute per agent. It resends identical requests on 429, 500, 502, 503 and 504, so the agent does not see a transient error, and logs every request to `llm_governor.jsonl`.
- Tools: a shell, git, uv, Node.js and npm, `./larderctl` (status, restart, logs, time left), each repo's tests, and the cx CLI with the team's read-only key.
- `tools/review` asks an independent reviewer (same model, separate OS user, frozen snapshot, no harness access) for a 0 to 100 score with must-fix items. `tools/ship` records the score the agent accepts. Review is advisory: the agent can restart services with unreviewed code.
- `AGENT_INSTRUCTIONS.md` is rendered from `agent-kit/AGENT_INSTRUCTIONS.template.md` with `RUN_MINUTES` = 180: the agent owns production and the backlog, the backlog exceeds the time available, choosing is part of the job, changes take effect only after `./larderctl restart`, and `REPORT.md` is due when about 5 minutes remain.

### 3. Create the Coralogix teams

1. Create 20 EU1 teams, one per condition for pairs 1 to 10, each with a team quota of at least 8. The second batch reuses the same 20 teams; every asset and query filters on `bench_run_id`.
2. Copy `harness/config/batch_teams.example.json` to `harness/config/batch_teams.json` and fill in `team_id` (and `team_name`) for each pair and condition.
3. In each team, create an operator API key with these presets: `Alerts`, `APIKeys`, `APM`, `Auditing`, `Dashboards`, `DataMap`, `DataQuerying`, `DataUsage`, `Enrichments`, `Events2Metrics`, `Extensions`, `Incidents`, `Integrations`, `LiveTail`, `OutboundWebhooks`, `ParsingRules`, `RecordingRules`, `SavedViews`, `TCOPolicies`, `TeamAdmin`, `DataSetup`, `Actions`, `DataAnalytics`, `NotificationCenter`, `SLO`, `Cases`, `Olly`. Add it as a cx profile named `larder-op-basic-<NN>` or `larder-op-slo-<NN>` (`<NN>` is the two-digit pair number):

```sh
cx profiles add --name larder-op-basic-01 --region eu1 --api-key <operator key>
```

4. Create each team's Send-Your-Data ingest key and read-only agent key with its operator key, and point its archives at your buckets. Then check until every team reports READY, including zero SLOs in every team without SLOs:

```sh
uv run python -m harness.ec2.batch setup-accounts --pairs 1-10
uv run python -m harness.ec2.batch check --pairs 1-10
```

The agent key's permissions are `AGENT_READ_PERMISSIONS` in `harness/bench.py`. SLO teams add `slo:ReadConfig` and `alerts:SloMgmtReadConfig`.

### 4. Study the Larder shop

Larder is an online grocery shop of 21 services in two legacy monorepos (`baseline/commerce`, `baseline/ops`). They call each other over HTTP through a harness-owned mesh, and every service and the mesh send logs, metrics and traces over OTLP/HTTP to Coralogix.

- `commerce/` (Python 3.11, Flask, waitress): edge-api, catalog, search, pricing, promotions, cart, checkout, accounts, loyalty, recommendations.
- `ops/` (Node.js, Express): orders, inventory, slots, payments, fraud, ledger, tax, webhooks, warehouse, notifications, and a homegrown event bus.
- Harness processes the agent cannot change: the mesh with one ingress proxy per service and the customer edge on port 8000, a supervisor (control API on 7070), the load generator, and three simulators (card payment provider, warehouse partner webhooks, email provider), all in `harness/`.
- Seed data: 1,151 products, the customers and delivery zones in `baseline/commerce/seed`, and the workload data in `harness/workload/data`. `harness/seed/generate_seed.py` generates them.
- Telemetry labels: application `larder`, subsystem = service name, `bench_run_id` = the run id.
- SLI source, the mesh: `larder_ingress_requests_total{service,route,method,code,code_class,reason}`, `larder_ingress_request_duration_seconds`, and `larder_ingress_entity_requests_total{entity_type,entity,code_class}` per product SKU, customer and postcode.
- Backlog in `agent-kit/common/BACKLOG.md`: FEAT-001 to FEAT-100, operational tickets REL-1 to REL-5, and TD-1 and TD-2.
- 16 seeded defects, identical in every trial: SF-1 to SF-7 in `commerce`, FF-1 to FF-6 in `ops`, and HC-1 to HC-3, which affect single products, customers or postcodes.

The agent must never see the seeded defects. This README, the hidden acceptance tests in `harness/evaluator/acceptance/`, the reference fixes in `harness/evaluator/reference/` and `harness/evaluator/hc_entities.json` are evaluator-only. Keep them out of every agent workspace and every agent session. On the trial instances the agent and the reviewer run as separate OS users who cannot read the harness.

The 16 seeded defects. A defect counts as fixed when every hidden test tagged with its ID passes on the final code; each test fails on the baseline and passes with the reference fix in `harness/evaluator/reference/<ID>.patch`.

| ID | Defect | Fixed when |
|---|---|---|
| SF-1 | Prices are float pounds and the local settings select a pricing engine that truncates: about 4% of checkouts fail with `price_mismatch` (REL-1). | Cart totals follow the integer-pence rule and those carts check out. |
| SF-2 | Two coupon expiry formats raise uncaught errors: 500s on 4 of 12 coupons. | No coupon 5xx; valid coupons accepted, expired ones rejected. |
| SF-3 | The search cache is unbounded; at 400 entries it rebuilds the index under a global lock, stalling search and product pages for seconds (REL-5). | 450 unique searches: no product page over 2.5 s, at most 1 search over 2.5 s. |
| SF-4 | The shared HTTP client has no timeout and retries POSTs; orders does not deduplicate `order_ref`: duplicate orders and captures. | At most 1 order and 1 capture per `order_ref` under a slow order path. |
| SF-5 | Recommendations make 13 uncached catalog calls per product view, past the mesh. | 16 concurrent viewers: detail p50 under 75 ms, p95 under 150 ms; listing p95 under 250 ms. |
| SF-6 | The account page lists orders without paging, and that branch returns every order in the shop. | The list returns only the customer's orders (fixed in orders). |
| SF-7 | The cart service keeps every cart and price snapshot forever; its memory grows until a memory guard restarts it, and each restart reloads everything. | Under 16 MB growth over 900 abandoned carts. |
| FF-1 | Inventory checks and writes stock without a lock: oversell, then 500s (REL-4). | Concurrent orders never oversell; no 5xx. |
| FF-2 | The payment adapter retries timeouts without an idempotency key: duplicate captures while the card provider is slow (REL-3). | At most 1 capture per order with a slow provider. |
| FF-3 | The slots pool leaks a connection on every `slot_full` rejection, so holds and checkouts later time out (REL-2). | After 45 `slot_full` holds, normal holds and checkouts finish in under 2 s. |
| FF-4 | The webhook dispatcher retries every failed item every tick and rewrites the whole queue. | At most 15 attempts per event in a 60 s outage; all delivered after recovery. |
| FF-5 | The bus delivers subscriber by subscriber and retries forever, so slow email blocks other topics. | Each order reaches the partner within 6 s with an 8 s email provider. |
| FF-6 | Fraud rereads and rewrites an ever-growing history file on every score, and fails closed after 500 ms. | Fraud latency per 100 calls at most max(25 ms, 3 times the first window). |
| HC-1 | A formatting bug crashes the product page for the 6 of 1,151 SKUs whose description contains `%`. | The six product pages return the seed data. |
| HC-2 | Account and checkout return 500 for the 20 customers with an apostrophe in their surname. | All 20 account pages return the seed data; 5 of them check out. |
| HC-3 | Slot listing returns 500 for the 9 customers at 8 new-estate postcodes. | Every new-estate postcode lists slots, and the 9 customers each hold one. |

The affected SKUs, customers and postcodes are in `harness/evaluator/hc_entities.json`.

To regenerate the seed data, run `uv run python harness/seed/generate_seed.py`. The output is identical on every run (seed 1337).

### 5. Review the workload

`harness/workload/scenario.yaml` drives the customers, deterministic from seed 1337. Every agent faces the same starting bugs and the same scheduled disruptions.

- Up to 64 concurrent sessions, 30 s client timeout, think time 0.3 to 2.0 s.
- Sessions browse, view 1 to 3 product pages across the whole catalogue, search, fill a cart of 1 to 6 items and pick a delivery slot (2.5% pick a full slot). 55% of slot holders check out, 2% of them with a declining card.
- 70% of confirmed orders are dispatched about 3 minutes later. Warehouse partner webhooks fail 10% of the time by default.
- Warm-up, 15 minutes before handover: 40 sessions per minute, baseline mix, no disruptions.
- Then one hour, repeated three times from handover:
  - Minutes 0 to 10, steady: 40 sessions per minute.
  - Minutes 10 to 20, promotion: 80 sessions per minute, 45% of sessions apply coupons, 60% of carts hold featured or 3-for-2 items.
  - Minutes 20 to 35, card provider degraded: 45 sessions per minute, payment latency p50 1.5 s and p99 6 s, 6% slow successes at 9 s, 1% errors.
  - Minutes 35 to 60, evening: 55 sessions per minute, partner webhook failures 35%, email latency p50 2.5 s and p99 8 s.

### 6. Configure the two conditions

Keep code, data, backlog, workload, disruptions, model, tools, permissions, time and request budget identical. The instructions differ only in the condition block.

- Without SLOs (`agent-kit/basic`): the Monitoring paragraph only. Assets: the dashboards Larder · Service overview and Larder · Runtime and dependencies, and 8 alerts: customer API 5xx > 5%, checkout failure > 10%, customer p95 > 2 s, service down, internal 5xx > 5%, Node event-loop lag > 200 ms, webhook queue > 300, bus backlog > 200.
- With SLOs (`agent-kit/slo-guided`): the same paragraph plus a section pointing to `SLO_CATALOG.md`, `tools/slo-status` (live 2, 10 and 60 minute burn rates, remaining rolling 60-minute budget, the Coralogix SLO engine's attainment and budget, firing SLO alerts), the SLO alerts, the dashboard Larder · SLOs and error budgets, and `cx slos list` / `cx slos get`. The block ends with one sentence: "Consider these SLOs in your decision making when deciding to fix, manage technical debt, or build a new feature."
- SLO assets: the same two dashboards and 8 alerts, plus 14 SLOs from `harness/cx/sli.py`, the SLO dashboard and 70 SLO alerts defined in `harness/cx/assets.py`: 3 dashboards and 78 alerts in total. All 14 SLOs use the mesh metrics both agents can query, scoped to `bench_run_id`, over a 1-day window.
  - Checkout success 99%: `POST /api/checkout` not 5xx, timeout or `price_mismatch`. Declines and genuine out-of-stock count as good.
  - Customer API availability 99.5%. Catalogue browse latency 99% within 500 ms. Search latency 95%. Delivery slot availability 99%. Order confirmation availability 99.5%. Payments availability 99.5%. Orders API latency 98%.
  - Grouped by entity: Product page availability per product 99%, Customer experience per customer 98%, Delivery slot availability per postcode 99%.
  - Per-minute window: Checkout latency minutes 85% (p95 at most 4 s), Customer error-free minutes 90% (at most 1% 5xx), Account page latency minutes 95% (p95 at most 1 s).

`./bench prepare` removes every `Larder · ` asset in the team and deploys the condition's set (`harness/cx/deploy.py`, `harness/cx/assets.py`). `./bench verify-isolation <run-id>` must pass before handover: it checks that the workspace, instructions, account assets and agent key expose exactly that condition.

### 7. Check the evaluator

Before any trial, check that the hidden tests score the baseline and the reference fixes as expected. Each build starts the 21 services locally on fresh data, so it needs Node.js and npm (to install `baseline/ops` dependencies) and takes several minutes.

```sh
uv run python -m harness.evaluator.reference_build --select none --out /tmp/ref-baseline --evaluate
uv run python -m harness.evaluator.reference_build --select defects --out /tmp/ref-defects --evaluate
```

The baseline (no patch selected) must fix 0 of 16 defects and complete 0 features. The defects build applies the 16 defect patches and must fix all 16. `uv run pytest harness/tests` runs the harness unit tests.

### 8. Launch the 40 trials

Run 20 pairs in two batches of 10. Both trials of a pair start at the same moment on separate c7i.2xlarge instances (8 vCPU, 16 GiB, Amazon Linux 2023, 30 GB gp3, IMDSv2 only, no inbound rules), from the same bundle, with seed 1337, a 15-minute warm-up and 180 minutes of agent time, each into its own Coralogix team.

Waves of 3 pairs (6 agents) start 200 minutes apart, so agents x 22 requests per minute stays under your endpoint's rate limit. EventBridge Scheduler starts each instance shortly before its wave. Leave `--max-minutes` at its default of 210. Start the second batch at least 600 minutes after the first, when the first batch's last wave starts.

```sh
uv run python -m harness.ec2.batch launch --schedule --batch <batch-id> --start <UTC ISO time> --pairs 1-10 \
  --rpm 22 --wave-size 3 --wave-spacing-min 200 --prepare-args "--run-min 180"
uv run python -m harness.ec2.batch watch --batch <batch-id> --pairs 1-10 --expect 20
```

After both batches:

```sh
uv run python -m harness.ec2.launch cleanup
```

`harness/ec2/bootstrap.sh` is the instance user data. It installs Node.js 26.7.0, uv, opencode 1.18.35 and the cx CLI, creates the `bench`, `agent` and `reviewer` OS users, runs the trial, uploads results to S3, deletes its credentials and terminates the instance. A dead-man shutdown caps its lifetime. On each instance it runs `./bench prepare --condition basic|slo-guided --run-min 180`, then `./bench verify-isolation`, `./bench run`, `./bench collect`, `./bench evaluate`, `./bench score` and `./bench teardown`.

- `./bench run` starts the 21 services, mesh, simulators and load generator, hands over at the end of warm-up and sends SIGTERM to the agent at the deadline. Traffic continues until the deadline even if the agent stops early.
- `./bench evaluate` runs the final code on fresh data and separate ports against the hidden acceptance suite and the visible tests.

### 9. Check validity and record trials

A trial is invalid if any model or API error reaches the agent's session, or any model request fails. The governor logs every model request with its final status (after its own retries) to `llm_governor.jsonl`, including transport failures; one request with a status of 400 or more makes the trial invalid, even if the client later resent it successfully. `harness.bench.agent_integrity` records the check in `agent_integrity` in each `run.json`. Exclude the whole pair on this check alone, never on its outcome. `harness.evaluator.pair_summary` applies the rule and prints the reasons; run a replacement pair for each excluded one.

Keep every agent transcript.

Each trial writes:

- `run.json`: `handover_at`, `deadline_at`, seed, warm-up and run minutes, `agent_integrity`.
- `agent_transcript.log`: the full opencode JSON event stream, including tokens per step.
- `artifacts/`: `ingress.jsonl` (every mesh request), `loadgen.jsonl` (every customer step), `psp_ledger.json` (every charge, with `order_ref` and status), `llm_governor.jsonl`, `reviews.jsonl`, `ship_decisions.jsonl`, supervisor events, resource samples and service logs.
- `reviews/`, `diffs/` and commit logs, `final_code/`, `REPORT.md`.
- `evaluation.json` (hidden tests per feature and per defect), `collected.json` (including checkout success per 10-minute bucket), `cx_manifest.json` (the Coralogix assets deployed).
- `results/<run-id>.json` per trial (`./bench score`): `features.<id>.acceptance_met` (every hidden test of the feature passes), `defects.<id>.fixed` (every hidden test tagged with the defect passes), `totals`, and `business_outcomes` (checkouts from the load generator's view).

### 10. Compute the outcome figures

Run these from this folder after both batches are fetched into `runs/` and `results/`. Pair numbers repeat across the two batches, so pairs are keyed by (batch, pair). `export_study_asset_3h` reads `results/pair_summary_flash.json` at that fixed path. Medians are over 20 trials per condition (with 20 values, the mean of the two middle values), with exact two-sided Wilcoxon signed-rank p values over the pairs: zero differences dropped, tied ranks averaged, no multiple-comparison correction. Percentage changes compare the two condition medians.

```sh
mkdir -p results/assets
uv run python -m harness.evaluator.pair_summary --batch <batch-a> --batch <batch-b> --out results/pair_summary_flash.json
uv run python -m harness.evaluator.export_study_asset_3h results/assets/slo-agent-study.json
uv run python -m harness.evaluator.export_slo_revenue --study-asset results/assets/slo-agent-study.json --out results/assets/slo-revenue.json
uv run python -m harness.evaluator.export_slo_payment_timeline --study-asset results/assets/slo-agent-study.json \
  --revenue-asset results/assets/slo-revenue.json --out results/assets/slo-payment-timeline.json
uv run python -m harness.evaluator.audit_checkout_mechanics --study-asset results/assets/slo-agent-study.json \
  --priorities-out results/assets/slo-reliability-priorities.json
```

### 11. Compute timing and behaviour

```sh
uv run python -m harness.evaluator.export_story_behavior --asset results/assets/slo-agent-study.json --out results/assets/slo-agent-behavior.json
uv run python scripts/export_slo_feature_delivery.py
uv run python scripts/slo_recovery.py
```

`export_slo_feature_delivery.py` and `slo_recovery.py` read and write `LARDER_ASSETS_DIR` (default `results/assets`). Add `--check` to `export_slo_revenue`, `export_slo_payment_timeline` and `scripts/slo_recovery.py` to verify existing outputs.

### 12. Audit diagnosis by hand

1. Read all 40 transcripts. For the product-page defect (HC-1) in each trial, find the first correct causal explanation (agent text or a task-plan line) and the first successful targeted code change (an `edit` or `write` to `services/edge_api/shaping.py` that stops formatting the product text). Silent edits count, and the change need not complete the repair. Reading an affected ID or mentioning an error does not count.
2. Record the selections, with the transcript line of each, in `docs/slo-hc-diagnostics-audit-01-10.json` (pairs 1 to 10) and `docs/slo-hc-diagnostics-audit-11-20.json` (pairs 11 to 20). Pair numbers follow `session_context.pair_order` in the study asset. The two `.example.json` files next to them show the fields the validator reads. Use `null` where there is no such event.
3. Validate the selections against the transcripts and the final tests, and write `docs/slo-hc-diagnostics-cohort.json`:

```sh
uv run python -m harness.evaluator.hc_diagnostics_cohort --write
```

The validator takes the earlier of the two events per trial. Unresolved causes stay censored at the deadline. Use the manual read only, never first exposure to an affected ID or any automated proxy. `make score` runs this step last and copies the cohort to `results/assets/`.

### 13. Compare with the page

Compare each figure in the next section, per condition, with the results page.

## How each figure is computed

Every figure compares 20 trials without SLOs with 20 trials with SLOs, from valid pairs only. The study asset is `results/assets/slo-agent-study.json` from `harness/evaluator/export_study_asset_3h.py`, unless a bullet names another file. Percentage changes compare the two condition medians (`change_pct`).

- **Revenue processed** (At a glance, summary table): per trial, the sum of the first captured charge per order in `psp_ledger.json` between handover and deadline, excluding duplicate charges and warm-up captures; the median per condition and the change of the medians. `harness/evaluator/export_slo_revenue.py`, `slo-revenue.json` (`median_gbp`, `median_increase_pct`).
- **Successful checkouts**: checkouts that succeeded from the load generator's view; median, change and paired p. Study asset `headline.paid`.
- **Minutes with more than 10% checkout errors** (minutes below 90% reliability): 10 times the number of 10-minute buckets whose checkout success was below 90%; median, change and p. Study asset `headline.minutes_below_90`.
- **Checkout server errors**: checkout responses classified as errors; median and p. Study asset `headline.checkout_errors`.
- **Working features after three hours** (features passing hidden tests): features whose every acceptance test passes on the final code on fresh data; median and p. Correctness is checked once, at the end. Study asset `headline.features`.
- **Defects fixed (of 16)**: seeded defects whose every tagged hidden test passes on the final code; median and p. Study asset `headline.defects_fixed`.
- **Trials that fixed the product, delivery or account bug**: trials whose final code passes every hidden test for HC-1, HC-3 and HC-2. Study asset `high_cardinality`.
- **Runs above 10% checkout errors** (and the error shading behind the dot plots): per 10-minute bucket, the trials whose checkout success was below 90% out of the trials with checkouts in that bucket. Study asset `timeline.<condition>.above_10pct_errors` and `.reporting`.
- **The hourly schedule**: the recorded workload phases of one cycle, identical in every trial. Study asset `session_context.phases`.
- **When agents completed features**: the first completion declaration per feature and trial, reconstructed from full transcripts by matching oversized `tools/ship` commands to successful server records (the completion log keeps at most 12 IDs per request), with repeats counted once. This counts agent completion declarations, not continuous test passes. `scripts/export_slo_feature_delivery.py`, `slo-feature-delivery.json`.
- **Cumulative captured transactions**: every 10 minutes from 0 to 180, the median of the 20 cumulative per-trial counts of captured card transactions, with the same deduplication as revenue. These are captured charges, distinct from successful checkout responses. Each endpoint is checked against the revenue figures. `harness/evaluator/export_slo_payment_timeline.py`, `slo-payment-timeline.json`.
- **Failed delivery-slot holds in the first hour**: primary slot-hold requests from the load generator in the first 60 minutes after handover that ended in an error or timeout, with valid `slot_full` rejections and `hold_retry` requests excluded, summed across all 20 trials per condition (totals, not medians), and the reduction. `harness/evaluator/audit_checkout_mechanics.py`, `slo-reliability-priorities.json`.
- **When agents committed reliability fixes**: every commit in either repository between handover and deadline whose subject carries an explicit repair marker (a REL, HC, SF or FF ticket, an INC tag, or a word such as fix, crash, leak or bug), no feature ID and no mention of review or follow-up. `harness/evaluator/export_story_behavior.py`, `slo-agent-behavior.json` (events of kind `fix`).
- **Recovery time**: onset is the first 10-minute bucket at or after minute 20 with checkout success below 90% (a serious failure); recovery is the first later bucket at or above 90%. The median over the affected trials that recovered, the number affected, and the change. Study asset `mttr`.
- **The first 40 minutes of recovery**: each affected trial aligned at its first serious-failure bucket at or after minute 20, then the pointwise median checkout error rate at +0, +10, +20, +30 and +40 minutes. The healthy line is at most 1% errors, inclusive. `scripts/slo_recovery.py`, `slo-recovery-curves.json`.
- **Product-page cause found within five minutes**: the number of trials per condition whose first correct causal explanation or successful targeted change for HC-1 came within five minutes of handover, from the manual read. `harness/evaluator/hc_diagnostics_cohort.py`, `docs/slo-hc-diagnostics-cohort.json` (`summary.<condition>.identified_by_5_minutes`).
- **Product-page detection time**: the median of the same times over all 20 trials per condition, with unresolved causes censored at the deadline; when the 10th or 11th time is censored, the median is reported as censored. Same script and file (`median_minute`).
- **Tokens per hour by checkout state**: total tokens from the transcript's `step_finish` events (input plus output, including repeated context). Each 10-minute bucket is assigned a checkout state (at most 1% errors, over 1% to 10%, over 10%), and all tokens in that state are divided by all hours in that state across the 20 trials. Input and output tokens are measured; thinking tokens are not measured separately. Study asset `tokens.<condition>`.
- **Rise in token use when checkout errors reached 1% to 10%**: the over 1% to 10% rate divided by the at most 1% rate, minus one. Study asset `tokens.<condition>.rise_1_to_10pct_vs_at_most_1pct`.
- **Total tokens (millions)**: the median per trial of the same total. Study asset `tokens.total_median_millions`.
- **Month measured**: the month of the first handover. Study asset `measured`.

## Layout

```
Makefile, .env.example   entry points and settings
pyproject.toml, uv.lock  the harness's Python dependencies
bench                    the operator CLI (harness/bench.py): doctor, agent-key, prepare, start, handover, run, status,
                         stop, collect, evaluate, score, verify-isolation, teardown; trial instances run prepare,
                         verify-isolation, run, collect, evaluate, score and teardown
baseline/                the Larder shop the agent works on: commerce/ (Python) and ops/ (Node.js), with seed data
                         and visible tests
agent-kit/               what the agent sees: the instruction template, the two condition blocks, the backlog,
                         larderctl and the tools
harness/runtime/         supervisor, mesh, service registry, LLM governor and review server
harness/sims/            payment, partner and email simulators
harness/workload/        the load generator, its scenario and its data
harness/seed/            the seed data generator
harness/cx/              SLI definitions, dashboards, alerts, SLOs and SLO alerts, and their deployment to Coralogix
harness/ec2/             team setup, batch launch and collection on EC2 (batch.py, launch.py, bootstrap.sh)
harness/evaluator/       evaluator-only: hidden acceptance tests, reference fixes, evaluation, collection, scoring,
                         isolation checks and the figure exporters
harness/config/          batch_teams.example.json for the team list; accounts.env.example for running ./bench on one
                         machine (on EC2, harness/ec2/launch.py writes this file for each trial)
harness/tests/           unit tests for the harness (uv run pytest harness/tests)
scripts/                 feature delivery reconstruction and the recovery curve
docs/                    templates for the two manual diagnosis audit manifests; the filled manifests and the
                         cohort are written here
runs/, results/          created by make run and make score; not committed
```
