.PHONY: run update-prod venv docs-serve docs-build frontend-install frontend-build test-coverage lint format

PORT ?= 20000
INSTANCE_HOST ?= 0.0.0.0
VENV ?= .venv
PYTHON := $(VENV)/bin/python
DOCKER ?= docker
MKDOCS_IMAGE ?= squidfunk/mkdocs-material
NPM ?= npm
PY_FILES := app tests scripts server.py api_check.py

venv:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	@if [ -s requirements.txt ]; then $(PYTHON) -m pip install -r requirements.txt; fi
	@if [ -s requirements-dev.txt ]; then $(PYTHON) -m pip install -r requirements-dev.txt; fi

run: $(VENV)/bin/python
	INSTANCE_HOST=$(INSTANCE_HOST) PORT=$(PORT) $(PYTHON) server.py

$(VENV)/bin/python:
	$(MAKE) venv

frontend-install:
	cd frontend && $(NPM) install

frontend-build:
	cd frontend && $(NPM) run build

update-prod: $(VENV)/bin/python frontend-build
	@echo "Checking production FTP upload access…"
	$(PYTHON) scripts/deploy_ftp.py

test-coverage: $(VENV)/bin/python
	$(PYTHON) -m pytest tests/ -q --cov=app --cov-report=term-missing --cov-fail-under=80

lint: $(VENV)/bin/python
	$(VENV)/bin/flake8 $(PY_FILES)
	$(VENV)/bin/isort --check-only $(PY_FILES)
	$(VENV)/bin/black --check $(PY_FILES)

format: $(VENV)/bin/python
	$(VENV)/bin/isort $(PY_FILES)
	$(VENV)/bin/black $(PY_FILES)

docs-serve:
	$(DOCKER) run --rm -it -p 8000:8000 -v "$(CURDIR):/docs" -w /docs $(MKDOCS_IMAGE)

docs-build:
	$(DOCKER) run --rm -v "$(CURDIR):/docs" -w /docs $(MKDOCS_IMAGE) build -s
