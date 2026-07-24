.PHONY: run update-prod venv

PORT ?= 8080
INSTANCE_HOST ?= 127.0.0.1
INDEX_PATH ?= $(CURDIR)/index.html
VENV ?= .venv
PYTHON := $(VENV)/bin/python

venv:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	@if [ -s requirements.txt ]; then $(PYTHON) -m pip install -r requirements.txt; fi

run: $(VENV)/bin/python
	INSTANCE_HOST=$(INSTANCE_HOST) PORT=$(PORT) INDEX_PATH=$(INDEX_PATH) $(PYTHON) server.py

$(VENV)/bin/python:
	$(MAKE) venv

update-prod:
	$(PYTHON) scripts/deploy_ftp.py
