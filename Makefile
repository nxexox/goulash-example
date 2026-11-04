VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8000

.PHONY: venv db run lint fmt test crawl work stats seed load-seed import-poc registry discover cycle docker

$(PY):
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -e . pytest ruff

venv: $(PY)

db:  ## local PostgreSQL on :5439
	docker compose up -d db

run: $(PY)
	$(VENV)/bin/uvicorn goulash.web.app:app --reload --reload-dir goulash --port $(PORT)

lint: $(PY)
	$(VENV)/bin/ruff check goulash tests
	$(VENV)/bin/ruff format --check goulash tests

fmt: $(PY)
	$(VENV)/bin/ruff format goulash tests
	$(VENV)/bin/ruff check --fix goulash tests

test: $(PY)
	$(PY) -m pytest -q

crawl: $(PY)  ## catalog walk + supplier sites; PAGES=listing pages per source
	$(PY) -m goulash crawl --pages $(or $(PAGES),5) --max-jobs $(or $(MAX_JOBS),300)

work: $(PY)
	$(PY) -m goulash work --max-jobs $(or $(MAX_JOBS),300)

registry: $(PY)  ## download the newest FNS SME registry (~2.2 GB, resumable) and import food companies
	$(PY) -m goulash registry-import

cycle: $(PY)  ## the full background cycle the worker runs: catalogs, registry refresh, discovery
	$(PY) -m goulash cycle

discover: $(PY)  ## find websites for registry companies (domain guessing, or Yandex Search API if a key is set)
	$(PY) -m goulash discover --limit $(or $(LIMIT),100)

stats: $(PY)
	$(PY) -m goulash stats

seed: $(PY)  ## export current catalog to data/seed (committed, bootstraps empty databases)
	$(PY) -m goulash export-seed

load-seed: $(PY)
	$(PY) -m goulash load-seed

import-poc: $(PY)
	$(PY) -m goulash import-poc

docker:
	docker compose up --build web
