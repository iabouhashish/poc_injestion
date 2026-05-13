.PHONY: help install env phase1 phase2 run serve test lint clean-outputs clean-db demo-reset clean

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
	@echo "  make env              Copy .env.example → .env (fill in API keys before running)"
	@echo ""
	@echo "UI (recommended)"
	@echo "  make serve            Start the dashboard at http://localhost:8000"
	@echo "                        Runs the full pipeline (Monday.com, DB, evals, files)"
	@echo "                        Select a specific application or run all from the UI"
	@echo ""
	@echo "CLI (headless / scripted)"
	@echo "  make phase1           Run Phase 1: document extraction (all applications)"
	@echo "  make phase2           Run Phase 2: compliance review (all processed applications)"
	@echo "  make run              Alias for phase1"
	@echo ""
	@echo "  # Run a single application:"
	@echo "  python main.py --phase=1 --client-id=APP-2026-0301"
	@echo "  python main.py --phase=2 --client-id=APP-2026-0301"
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

serve:
	PYTHONPATH=. uvicorn api.main:app --reload --port 8000

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
