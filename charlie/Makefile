# Charlie & Lola - every command goes through this Makefile.
SHELL := /bin/bash
PY ?= .venv/bin/python
VENV ?= .venv
TEST_DB_PORT ?= 54329
export TEST_DATABASE_URL ?= postgresql://postgres:postgres@localhost:$(TEST_DB_PORT)/postgres
export PYTHONPATH := src

.PHONY: help venv install secrets lint fmt typecheck test test-unit test-db testdb testdb-stop \
        coverage-killswitch eval eval-live demo demo-down demo-scenario ci monthly-test clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | sort

venv: ## Create the virtualenv (Python 3.12)
	uv venv --python 3.12 $(VENV) || python3.12 -m venv $(VENV)

install: venv ## Install dependencies
	uv pip install --python $(PY) -e ".[dev,live]" || $(PY) -m pip install -e ".[dev,live]"

secrets: ## Generate local dev secrets into ./secrets (never real provider keys)
	$(PY) scripts/gen_secrets.py

lint: ## ruff
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts

fmt: ## Auto-format
	$(PY) -m ruff format src tests scripts
	$(PY) -m ruff check --fix src tests scripts

typecheck: ## mypy --strict
	$(PY) -m mypy --strict src

testdb: ## Start a throwaway Postgres 16 for tests (docker)
	@if ! docker ps --format '{{.Names}}' | grep -q '^charlie-testdb$$'; then \
	  docker rm -f charlie-testdb >/dev/null 2>&1 || true; \
	  docker run -d --name charlie-testdb -e POSTGRES_PASSWORD=postgres -p $(TEST_DB_PORT):5432 postgres:16 >/dev/null; \
	fi
	@for i in $$(seq 1 60); do docker exec charlie-testdb pg_isready -U postgres >/dev/null 2>&1 && exit 0; sleep 1; done; echo "testdb not ready"; exit 1

testdb-stop:
	docker rm -f charlie-testdb

test: testdb ## All tests (unit + database + killswitch 100% coverage)
	CHARLIE_REQUIRE_DB=1 $(PY) -m pytest
	$(MAKE) coverage-killswitch

test-unit: ## Tests that need no database
	$(PY) -m pytest -m "not db"

coverage-killswitch: ## killswitch/core.py must have 100 % line+branch coverage
	$(PY) -m pytest tests/killswitch -q --cov=assistants.killswitch.core --cov-branch \
	  --cov-report=term-missing --cov-fail-under=100 -p no:cacheprovider

eval: ## Adversarial corpus against the whole pipeline (mock LLM, obedient + compromised)
	$(PY) -m assistants.eval.runner --llm mock

eval-live: ## Same corpus against the real Anthropic API (needs ANTHROPIC_API_KEY; costs money)
	$(PY) -m assistants.eval.runner --llm anthropic

demo: secrets ## Build and start all services with Docker Compose, then run the demo scenario
	docker compose up -d --build
	$(PY) scripts/wait_healthy.py
	$(MAKE) demo-scenario

demo-scenario: ## End-to-end scenario against the running stack (simulated phone/devices)
	$(PY) scripts/demo.py --base http://127.0.0.1:8080

demo-down:
	docker compose down -v

monthly-test: ## Monthly drill: alerts, backup restore, duress code (simulated devices)
	$(PY) -m assistants.ops.monthly --sim

ci: lint typecheck test eval ## Everything CI runs

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache .hypothesis .coverage htmlcov
