# Larder commerce
# usage: make test | make run-all | make run SVC=catalog | make lint

SVC ?= catalog

.PHONY: test run run-all lint smoke reset clean

test:
	LARDER_ENV=test uv run pytest -q

# all ten services on their app ports (no mesh), see scripts/run_all.py
run-all:
	uv run python -m scripts.run_all

run:
	LARDER_SERVICE=$(SVC) LARDER_DATA_DIR=./var/$(SVC) uv run python -m services.$(subst -,_,$(SVC))

lint:
	uv run flake8 services libs legacy tests
	uv run black --check services libs

smoke:
	uv run python -m scripts.smoke http://127.0.0.1:8101

# wipes local dbs; each service re-seeds on next start
reset:
	uv run python -m scripts.reset_data

clean: reset
	find . -name __pycache__ -prune -exec rm -rf {} +
