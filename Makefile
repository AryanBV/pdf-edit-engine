.PHONY: lint format typecheck test all clean

lint:
	ruff check src/ tests/
format:
	ruff format src/ tests/
typecheck:
	python -m mypy
test:
	python -m pytest -v --cov=pdf_edit_engine
all: lint typecheck test
clean:
	rm -rf dist/ build/ *.egg-info \
	       demo_output/ diagnose_output/ validate_output/ tmp/ \
	       .coverage .pytest_cache .mypy_cache .ruff_cache htmlcov/
