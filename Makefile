.PHONY: help install env phase1 phase2 run test lint clean-outputs clean-db demo-reset clean

PYTHON  := python
PIP     := pip
MAIN    := main.py

# ── Help ───────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "Crestview Capital Group — AI Onboarding Pipeline"
	@echo ""
	@echo "Setup"
	@echo "  make install          Install dependencies from pyproject.toml"
	@echo "  make env              Copy .env.example → .env (edit before running)"
	@echo ""
	@echo "Pipeline"
	@echo "  make phase1           Run Phase 1: document extraction (Agent 1)"
	@echo "  make phase2           Run Phase 2: compliance review (Agent 2)"
	@echo "  make run              Shortcut for phase1 (default entry point)"
	@echo ""
	@echo "Development"
	@echo "  make test             Run the full test suite"
	@echo "  make lint             Run ruff linter"
	@echo ""
	@echo "Cleanup"
	@echo "  make demo-reset       Clear the database for a fresh demo run"
	@echo "  make clean-outputs    Delete the outputs/ directory"
	@echo "  make clean-db         Delete the SQLite database"
	@echo "  make clean            Delete outputs/ and the database"
	@echo ""
	@echo "Examples"
	@echo "  make phase1"
	@echo "  make phase2"
	@echo "  make demo-reset"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────────────────
install:
	$(PIP) install -e ".[dev]" 2>/dev/null || $(PIP) install -e .

env:
	@if [ -f .env ]; then \
		echo ".env already exists — not overwriting."; \
	else \
		cp .env.example .env; \
		echo ".env created from .env.example — fill in your API keys before running."; \
	fi

# ── Pipeline ───────────────────────────────────────────────────────────────────
run: phase1

phase1:
	$(PYTHON) $(MAIN) --phase=1

phase2:
	$(PYTHON) $(MAIN) --phase=2

# ── Development ────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

# ── Cleanup ────────────────────────────────────────────────────────────────────
demo-reset:
	$(PYTHON) $(MAIN) --clear-db

clean-outputs:
	rm -rf outputs/

clean-db:
	rm -f data/crestview_pipeline.db

clean: clean-outputs clean-db
