.PHONY: help install dev test lint format build clean clean-all wheel i18n i18n-update

# Path to PySide6's `lupdate` / `lrelease`. Override via env if pyside6
# is installed somewhere unusual:
#     make i18n LUPDATE=/path/to/lupdate LRELEASE=/path/to/lrelease
PYSIDE_DIR := $(shell python -c 'import PySide6, os; print(os.path.dirname(PySide6.__file__))' 2>/dev/null)
LUPDATE  ?= $(PYSIDE_DIR)/lupdate
LRELEASE ?= $(PYSIDE_DIR)/lrelease

help:
	@echo "Refrain — common dev tasks"
	@echo ""
	@echo "  make install      Install Refrain into the active venv"
	@echo "  make dev          Install with dev extras (pytest, ruff, …)"
	@echo "  make test         Run the test suite"
	@echo "  make lint         ruff check + ruff format --check"
	@echo "  make format       Apply ruff format"
	@echo "  make wheel        Build the wheel + sdist into dist/"
	@echo "  make i18n         Regenerate .qm files from .ts (compile only)"
	@echo "  make i18n-update  Refresh .ts source-string lists from src/, then compile"
	@echo "  make clean        Remove build artifacts and bytecode caches"
	@echo "  make clean-all    Same as clean, plus the .venv"

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	PYTHONPATH=src pytest -q

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

wheel: clean
	python -m build

# Compile every i18n/refrain_*.ts to a matching .qm. Run after editing
# translations. .qm files are committed so the wheel ships them without
# requiring lrelease at install time.
i18n:
	@for ts in src/refrain/i18n/refrain_*.ts; do \
		qm="$${ts%.ts}.qm"; \
		"$(LRELEASE)" "$$ts" -qm "$$qm"; \
	done

# Refresh the .ts source-string list from the current Python sources,
# preserving existing translations. Use after wrapping new strings in tr().
i18n-update:
	@"$(LUPDATE)" -no-obsolete \
		src/refrain/app.py \
		src/refrain/ui/*.py \
		-ts $(wildcard src/refrain/i18n/refrain_*.ts)
	@$(MAKE) i18n

# Remove anything that pytest, ruff, hatch, pip, or coverage might leave
# behind in the project folder. Runtime data (logs, cover-art cache) lives
# under $XDG_STATE_HOME / $XDG_CACHE_HOME, NOT here.
clean:
	rm -rf build/ dist/ *.egg-info/ src/*.egg-info/
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/
	rm -rf htmlcov/ .coverage .coverage.*
	find . -path ./.venv -prune -o -name '__pycache__' -type d -print -exec rm -rf {} + 2>/dev/null || true
	find . -path ./.venv -prune -o -name '*.py[cod]' -print -delete 2>/dev/null || true

clean-all: clean
	rm -rf .venv/
