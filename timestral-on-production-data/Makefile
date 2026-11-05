# Thin wrappers over the folder's scripts. Settings come from .env (copy .env.example).
-include .env
export

PY := envs/timestral/bin/python
REPEATS ?= 10

.PHONY: help setup pull screen run cpu gpu timing demo score clean

help:  ## list the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

setup:  ## build the five model virtualenvs under envs/
	scripts/setup_envs.sh

pull:  ## fetch every series in config/series.json over the window into data/production/
	$(PY) scripts/fetch.py $${SERIES_FILE:-config/series.json}

screen:  ## print missing share, CV, trend R^2 and daily and weekly autocorrelation per series
	$(PY) scripts/screen.py

run: pull cpu gpu timing  ## pull, the CPU scoring pass, the GPU scoring run, then the timing run

cpu:  ## score the naive baselines and the classical models (classical cutoffs in parallel)
	scripts/run_cpu.sh naive stats

gpu:  ## launch the A10G instance, run the eight foundation models, copy results back, tear down
	infra/launch.sh
	infra/gpu_run.sh || true
	infra/teardown.sh

timing:  ## the separate timing run on this one machine: foundation models on CPU, then classical models one at a time
	scripts/run_cpu.sh timing

demo:  ## counter-reset and determinism checks on 5-minute OpenTelemetry demo data
	CX_PROFILE=$(DEMO_CX_PROFILE) FETCH_START=$(DEMO_START) FETCH_END=$(DEMO_END) FETCH_STEP=5m FETCH_OUT=data/demo \
	  $(PY) scripts/fetch.py $${DEMO_SERIES_FILE:-config/demo-series.json}
	$(PY) scripts/demo/bench.py
	$(PY) scripts/demo/bench.py $(DEMO_RESTART) post-restart
	for i in $$(seq 1 $(REPEATS)); do $(PY) scripts/demo/repeat.py $$i; done

score:  ## write every report, table and chart under results/
	$(PY) scripts/report_tsfm.py results/production/tsfm_cuda
	$(PY) scripts/report_tsfm.py results/production/tsfm_cpu
	$(PY) scripts/report_classical.py
	$(PY) scripts/table_classical.py
	$(PY) scripts/chart_week.py
	$(PY) scripts/week_models.py
	$(PY) scripts/weekly_cycle.py
	if [ -d results/demo ]; then $(PY) scripts/demo/report.py && $(PY) scripts/demo/report.py post-restart \
	  && $(PY) scripts/demo/compare_repeats.py; fi

clean:  ## tear down any cloud resources tagged PROJECT_TAG, then delete results/
	-infra/teardown.sh
	rm -rf results
