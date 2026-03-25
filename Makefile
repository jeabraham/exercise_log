# Makefile for exercise_log
#
# Usage:
#   make install                              – set up venv and run tests
#   make install-llm                          – install with Ollama LLM support
#   make install-sheets                       – install with Google Sheets support
#   make test                                 – run tests
#   make test-sheets                          – run Google Sheets integration tests
#   make run    INPUT=path OUTPUT=path        – watch CSV continuously
#   make run-once INPUT=path OUTPUT=path      – process CSV once and exit
#   make run-sheets INPUT=path                – watch and append to Google Sheet
#   make run-once-sheets INPUT=path           – process once and append to Google Sheet
#   make clean                                – remove venv and caches

VENV      := .venv
PYTHON    := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip

# Default delay (seconds) between file-change detection and processing
DELAY     ?= 5

# Allow the user to pass INPUT= and OUTPUT= on the command line
INPUT     ?=
OUTPUT    ?=

.PHONY: all install install-llm install-sheets test test-sheets run run-once run-sheets run-once-sheets clean help

all: install

## install: create venv, install deps (editable) including test deps, run tests
install: $(VENV)/bin/activate
	@echo "✓ Virtual environment ready.  Run 'make help' for available targets."

$(VENV)/bin/activate: pyproject.toml requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip --quiet
	$(PIP) install -e ".[dev]" --quiet
	$(MAKE) test
	@touch $(VENV)/bin/activate

## install-llm: create venv with Ollama LLM support (pyyaml + ollama package)
install-llm: pyproject.toml requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip --quiet
	$(VENV)/bin/pip install -e ".[dev,llm]" --quiet
	$(MAKE) test
	@touch $(VENV)/bin/activate
	@echo "✓ Virtual environment ready with LLM support.  Run 'make help' for available targets."

## install-sheets: create venv with Google Sheets support
install-sheets: pyproject.toml requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip --quiet
	$(VENV)/bin/pip install -e ".[dev,sheets]" --quiet
	$(MAKE) test
	@touch $(VENV)/bin/activate
	@echo "✓ Virtual environment ready with Google Sheets support.  Run 'make help' for available targets."

## test: run the test suite inside the venv
test: $(VENV)/bin/activate
	$(VENV)/bin/pytest tests/ -v

## test-sheets: run the Google Sheets integration tests (requires live credentials in config.yaml)
test-sheets: $(VENV)/bin/activate
	$(VENV)/bin/pytest tests/test_sheets.py -v -m sheets_integration

## run: watch INPUT csv and write new rows to OUTPUT (Ctrl-C to stop)
run: $(VENV)/bin/activate
	@test -n "$(INPUT)"  || (echo "ERROR: specify INPUT=<path/to/workouts.csv>"; exit 1)
	@test -n "$(OUTPUT)" || (echo "ERROR: specify OUTPUT=<path/to/parsed.csv>"; exit 1)
	$(PYTHON) -m exercise_log --input "$(INPUT)" --output "$(OUTPUT)" --delay $(DELAY)

## run-once: process INPUT csv once and exit
run-once: $(VENV)/bin/activate
	@test -n "$(INPUT)"  || (echo "ERROR: specify INPUT=<path/to/workouts.csv>"; exit 1)
	@test -n "$(OUTPUT)" || (echo "ERROR: specify OUTPUT=<path/to/parsed.csv>"; exit 1)
	$(PYTHON) -m exercise_log --input "$(INPUT)" --output "$(OUTPUT)" --once --delay $(DELAY)

## run-sheets: watch INPUT csv and append new rows to Google Sheet (Ctrl-C to stop)
run-sheets: $(VENV)/bin/activate
	@test -n "$(INPUT)" || (echo "ERROR: specify INPUT=<path/to/workouts.csv>"; exit 1)
	$(PYTHON) -m exercise_log --input "$(INPUT)" --delay $(DELAY)

## run-once-sheets: process INPUT csv once and append to Google Sheet, then exit
run-once-sheets: $(VENV)/bin/activate
	@test -n "$(INPUT)" || (echo "ERROR: specify INPUT=<path/to/workouts.csv>"; exit 1)
	$(PYTHON) -m exercise_log --input "$(INPUT)" --once

## clean: remove venv and Python caches
clean:
	rm -rf $(VENV) __pycache__ exercise_log/__pycache__ tests/__pycache__ \
	       *.egg-info .pytest_cache

## help: print this help
help:
	@echo ""
	@echo "exercise_log – Makefile targets"
	@echo "================================"
	@grep -E '^## ' Makefile | sed 's/^## /  /'
	@echo ""
	@echo "Variables (pass on the command line):"
	@echo "  INPUT=<path>   Path to the input CSV produced by the Siri shortcut"
	@echo "  OUTPUT=<path>  Path to the structured output CSV (not needed for Sheets targets)"
	@echo "  DELAY=<secs>   Seconds to wait after a file change (default: 5)"
	@echo ""
	@echo "Examples:"
	@echo "  make install"
	@echo "  make install-llm"
	@echo "  make install-sheets"
	@echo "  make run          INPUT=~/workouts.csv OUTPUT=~/parsed.csv"
	@echo "  make run-once     INPUT=~/workouts.csv OUTPUT=~/parsed.csv"
	@echo "  make run-sheets   INPUT=~/workouts.csv"
	@echo "  make run-once-sheets INPUT=~/workouts.csv"
	@echo "  make run          INPUT=~/workouts.csv OUTPUT=~/parsed.csv DELAY=10"
	@echo ""
