PY ?= $(HOME)/fixgraph-venv/bin/python
export FIXGRAPH_URL ?= http://127.0.0.1:18443
export FIXGRAPH_TOKEN ?= $(shell tr -d '\n' < /opt/fixgraph/auth-token 2>/dev/null)
export FIXGRAPH_GRAPH ?= default
export FIXGRAPH_CELL ?= cell-0
export FIXGRAPH_NAMESPACE ?= local

.PHONY: probe test bench bench-offline results seed-demo

probe:        ## round-trip every query shape against the live node
	$(PY) scripts/probe.py

test:         ## unit tests (offline); integration tests need the live node
	$(PY) -m pytest -q

bench:        ## full synthetic benchmark, 3 seeds -> results/results.json + chart
	$(PY) -m fixgraph.bench --tier synthetic --seeds 3 --out results

bench-offline: ## non-graph arms only (sanity check, no server needed)
	$(PY) -m fixgraph.bench --tier synthetic --seeds 1 --skip-graph --out results

real-exec:    ## execute real-tier scenarios in containers on the VM (once, cached)
	$(PY) -m fixgraph.bench.real_tier --execute --ssh ubuntu@158.180.57.167

real-bench:   ## real-tier benchmark from the execution cache
	$(PY) -m fixgraph.bench --tier real --out results

page:         ## single-file results page
	$(PY) scripts/make_results_page.py --out results/index.html
