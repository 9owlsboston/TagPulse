.PHONY: lint typecheck test format check run export-openapi migration-check

lint:        ## Run linter
	ruff check src tests

typecheck:   ## Run type checker
	mypy src

test:        ## Run unit tests
	pytest tests/unit -v --tb=short

migration-check:  ## Sprint 19: round-trip alembic upgrade head -> downgrade -1 -> upgrade head
	@if [ -z "$$TAGPULSE_INTEGRATION_DB_URL" ]; then \
	  echo "TAGPULSE_INTEGRATION_DB_URL not set; export it to a TimescaleDB URL first." >&2; \
	  exit 1; \
	fi
	pytest tests/integration/test_migration_round_trip.py -v --tb=short

format:      ## Auto-format code
	ruff format src tests
	ruff check --fix src tests

check: lint typecheck test  ## Run all quality gates

run:         ## Start development server
	# Sprint 17a geofence eval is gated by a rollout flag (off in prod by
	# default). Force-enable in dev so smoke-test zone rules fire.
	GEOFENCE_EVALUATION_ENABLED=true \
	uvicorn tagpulse.api.main:app --reload --host 0.0.0.0 --port 8000

export-openapi:  ## Export the FastAPI OpenAPI spec to openapi.json
	python -c "import json; from tagpulse.api.main import app; print(json.dumps(app.openapi(), indent=2, sort_keys=True))" > openapi.json
	@echo "Wrote openapi.json ($$(wc -c < openapi.json) bytes)"
