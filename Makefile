# Common tasks. Run `make help` for the list.
SHELL := /bin/bash
PYTHON ?= python3
VENV := backend/.venv
BIN := $(VENV)/bin

.PHONY: help setup serve dev demo check sync test autostart autostart-off

help: ## Show this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-7s %s\n", $$1, $$2}'

setup: ## Install everything, create backend/.env and the database, build the web app
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --quiet --upgrade pip
	$(BIN)/python -m pip install --quiet -e "./backend[dev]"
	$(BIN)/studyhub init
	cd web && npm install --no-fund --no-audit && npm run build

serve: ## Run StudyHub at http://127.0.0.1:8000
	$(BIN)/studyhub serve

autostart: ## macOS: start StudyHub at login and keep it running (and restart it now)
	$(BIN)/studyhub autostart on

autostart-off: ## macOS: stop the background StudyHub and don't start it at login
	$(BIN)/studyhub autostart off

dev: ## Backend with reload + Vite dev server at http://localhost:5173
	trap 'kill 0' INT TERM EXIT; $(BIN)/studyhub serve --reload & (cd web && npm run dev) & wait

demo: ## Load the example data set
	$(BIN)/studyhub demo

check: ## Test the logins in backend/.env
	$(BIN)/studyhub check

sync: ## Pull new material from every configured source
	$(BIN)/studyhub sync

test: ## Backend tests, web lint and build
	cd backend && .venv/bin/pytest -q
	cd web && npm run lint && npm run build
