.PHONY: help install dev test lint format build clean clean-all wheel

help:
	@echo "Refrain — common dev tasks"
	@echo ""
	@echo "  make install   Install Refrain into the active venv"
	@echo "  make dev       Install with dev extras (pytest, ruff, …)"
	@echo "  make test      Run the test suite"
	@echo "  make lint      ruff check + ruff format --check"
	@echo "  make format    Apply ruff format"
	@echo "  make wheel     Build the wheel + sdist into dist/"
	@echo "  make clean     Remove build artifacts and bytecode caches"
	@echo "  make clean-all Same as clean, plus the .venv"

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
