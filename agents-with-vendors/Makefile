# Thin wrappers over the folder's scripts. The scripts read .env themselves (copy .env.example).
.PHONY: help setup run score clean

help: ## list the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

setup: ## fetch and patch the shop, build images, start the stack, build each CLI's skills plugin
	scripts/setup-shop.sh
	agents/build-cli-plugins.sh

run: ## run the ten scenarios, 25 agents each (agents/run-all.sh, 11 to 18 hours)
	agents/run-all.sh

score: ## blind the transcripts, review them against results/ground-truth.yaml, map the marks back, print every figure
	python3 scoring/blind.py export results/runs results/blind results/blind-key.csv
	scoring/review.sh
	python3 scoring/blind.py unblind results/blind results/blind-key.csv results/scores.csv
	python3 scoring/figures.py results/scores.csv --json results/figures.json

clean: ## stop the stack and remove its volumes; results/ and otel-demo/ stay
	agents/ops.sh dc down -v
