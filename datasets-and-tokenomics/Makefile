# Thin wrappers over the folder's scripts. Copy .env.example to .env first.
-include .env
# The cx CLI the study used: the first build with `cx datasets list`, installed into .tools/ by `make setup`.
CX_CLI_REV ?= db204aeadb8533dc84a71496bd39663fdabd19c2
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export
.PHONY: help setup replay datasets run score clean
ARMS = none discover prompt

help: ## list the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

setup: ## install the pinned cx CLI, check the tools, then send one minute of replay and print how to verify it arrived
	cargo install --git https://github.com/coralogix/cx-cli --rev $(CX_CLI_REV) --root .tools --locked coralogix-cli
	@for t in python3 cx grpcurl opencode; do command -v $$t >/dev/null || { echo "missing: $$t"; exit 1; }; done
	cx datasets list >/dev/null
	python3 generator/generate.py check

replay: ## replay the profiled week live from RUN_START (7 days; run it in tmux or with nohup)
	python3 generator/generate.py run --start $(RUN_START)

datasets: ## create the 12 datasets, fill them from the replayed week, wait for the fills, verify them against raw data
	python3 datasets/build.py create
	python3 datasets/build.py fill
	python3 datasets/build.py status
	python3 datasets/build.py verify

run: ## compute the answer key from raw data, then run 3 arms x 100 questions x REPS (about 2 days at the defaults)
	python3 harness/truth.py
	for a in $(ARMS); do harness/arm.sh setup $$a && harness/arm.sh start $$a; done
	sleep 10
	python3 harness/run.py; status=$$?; for a in $(ARMS); do harness/arm.sh stop $$a; done; exit $$status

score: ## compute every published figure into results/study.json and print the headline table
	python3 analysis/score.py

clean: ## stop the arm servers and delete the 12 datasets; the replayed telemetry and results/ stay
	-for a in $(ARMS); do harness/arm.sh stop $$a; done
	python3 datasets/build.py delete
