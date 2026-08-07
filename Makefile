.PHONY: run update-prod venv docs-serve docs-build

PORT ?= 8080
INSTANCE_HOST ?= 127.0.0.1
VENV ?= .venv
PYTHON := $(VENV)/bin/python
DOCKER ?= docker
MKDOCS_IMAGE ?= squidfunk/mkdocs-material

venv:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	@if [ -s requirements.txt ]; then $(PYTHON) -m pip install -r requirements.txt; fi

run: $(VENV)/bin/python
	INSTANCE_HOST=$(INSTANCE_HOST) PORT=$(PORT) $(PYTHON) server.py

$(VENV)/bin/python:
	$(MAKE) venv

update-prod: $(VENV)/bin/python
	@echo "Checking production FTP upload access…"
	$(PYTHON) scripts/deploy_ftp.py

docs-serve:
	$(DOCKER) run --rm -it -p 8000:8000 -v "$(CURDIR):/docs" -w /docs $(MKDOCS_IMAGE)

docs-build:
	$(DOCKER) run --rm -v "$(CURDIR):/docs" -w /docs $(MKDOCS_IMAGE) build -s
