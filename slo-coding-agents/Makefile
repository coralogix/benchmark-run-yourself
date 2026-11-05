# Thin wrappers over the folder's scripts. Settings come from .env (copy .env.example).
-include .env
export

ASSETS ?= $(if $(LARDER_ASSETS_DIR),$(LARDER_ASSETS_DIR),results/assets)
PY = uv run --frozen python

.PHONY: help setup run score clean

help: ## list the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

setup: ## install the harness, set up the Coralogix teams, check them
	uv sync --frozen
	$(PY) -m harness.ec2.batch setup-accounts --pairs 1-10
	$(PY) -m harness.ec2.batch check --pairs 1-10

run: ## schedule both batches of 10 pairs on EC2 and wait for all 40 trials
	$(PY) -m harness.ec2.batch launch --schedule --batch $(LARDER_BATCH_A) --start $(LARDER_BATCH_A_START) --pairs 1-10 \
	  --rpm 22 --wave-size 3 --wave-spacing-min 200 --prepare-args "--run-min 180"
	$(PY) -m harness.ec2.batch launch --schedule --batch $(LARDER_BATCH_B) --start $(LARDER_BATCH_B_START) --pairs 1-10 \
	  --rpm 22 --wave-size 3 --wave-spacing-min 200 --prepare-args "--run-min 180"
	$(PY) -m harness.ec2.batch watch --batch $(LARDER_BATCH_A) --pairs 1-10 --expect 20
	$(PY) -m harness.ec2.batch watch --batch $(LARDER_BATCH_B) --pairs 1-10 --expect 20

score: ## compute every published figure into results/assets/ (the product-page diagnosis needs your manual read first, README step 12)
	mkdir -p $(ASSETS)
	$(PY) -m harness.evaluator.pair_summary --batch $(LARDER_BATCH_A) --batch $(LARDER_BATCH_B) --out results/pair_summary_flash.json
	$(PY) -m harness.evaluator.export_study_asset_3h $(ASSETS)/slo-agent-study.json
	$(PY) -m harness.evaluator.export_slo_revenue --study-asset $(ASSETS)/slo-agent-study.json --out $(ASSETS)/slo-revenue.json
	$(PY) -m harness.evaluator.export_slo_payment_timeline --study-asset $(ASSETS)/slo-agent-study.json \
	  --revenue-asset $(ASSETS)/slo-revenue.json --out $(ASSETS)/slo-payment-timeline.json
	$(PY) -m harness.evaluator.audit_checkout_mechanics --study-asset $(ASSETS)/slo-agent-study.json \
	  --priorities-out $(ASSETS)/slo-reliability-priorities.json
	$(PY) -m harness.evaluator.export_story_behavior --asset $(ASSETS)/slo-agent-study.json --out $(ASSETS)/slo-agent-behavior.json
	$(PY) scripts/export_slo_feature_delivery.py
	$(PY) scripts/slo_recovery.py
	@test -f docs/slo-hc-diagnostics-audit-01-10.json -a -f docs/slo-hc-diagnostics-audit-11-20.json \
	  || { echo "HC diagnosis: write docs/slo-hc-diagnostics-audit-01-10.json and -11-20.json first (README step 12)"; exit 1; }
	$(PY) -m harness.evaluator.hc_diagnostics_cohort --write
	cp docs/slo-hc-diagnostics-cohort.json $(ASSETS)/slo-hc-diagnostics-cohort.json

clean: ## remove the AWS role, security group, schedules, launch templates and leftover parameters
	$(PY) -m harness.ec2.launch cleanup
