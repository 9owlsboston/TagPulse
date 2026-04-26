.PHONY: lint typecheck test format check run

lint:        ## Run linter
	ruff check src tests

typecheck:   ## Run type checker
	mypy src

test:        ## Run unit tests
	pytest tests/unit -v --tb=short

format:      ## Auto-format code
	ruff format src tests
	ruff check --fix src tests

check: lint typecheck test  ## Run all quality gates

run:         ## Start development server
	uvicorn tagpulse.api.main:app --reload --host 0.0.0.0 --port 8000
