lint:
	ruff check src/ tests/
format:
	ruff format src/ tests/
typecheck:
	python -m mypy
test:
	python -m pytest -v --cov=pdf_edit_engine
all: lint typecheck test
