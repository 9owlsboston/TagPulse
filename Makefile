.PHONY: lint typecheck test format check run export-openapi migration-check \
        smoke rotate-key logs doctor demo-tenant demo-tenant-reset help

# Default ENV for ops targets — override on the command line: make logs ENV=prod
ENV ?= dev

lint:        ## Run linter (style + format check)
	ruff check src tests clients/pi
	ruff format --check src tests clients/pi

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
	ruff format src tests clients/pi
	ruff check --fix src tests clients/pi

check: lint typecheck test  ## Run all quality gates

run:         ## Start development server
	# Sprint 17a geofence eval is gated by a rollout flag (off in prod by
	# default). Force-enable in dev so smoke-test zone rules fire.
	GEOFENCE_EVALUATION_ENABLED=true \
	uvicorn tagpulse.api.main:app --reload --host 0.0.0.0 --port 8000

export-openapi:  ## Export the FastAPI OpenAPI spec to openapi.json
	# Sprint 28 H6: dedupe per-operation `security` lists. Some routes
	# depend on multiple ``APIKeyHeader``-typed security helpers (e.g.,
	# ``api_key_header`` + ``tenant_id_header`` in core/user_auth.py).
	# FastAPI emits one entry per dependency, but the global
	# ``components.securitySchemes`` only registers one ``APIKeyHeader``
	# scheme. CPython 3.12 happens to dedupe these identical entries
	# during dict construction; 3.11 doesn't, which produces a noisy
	# `git diff --exit-code` failure under the Sprint 28 drift gate.
	# Normalize here so the committed spec is stable regardless of the
	# generating interpreter.
	python scripts/export_openapi.py > openapi.json
	@echo "Wrote openapi.json ($$(wc -c < openapi.json) bytes)"

# ---------------------------------------------------------------------------
# Sprint 28 F1 — operator targets. Every target accepts ENV=<dev|staging|prod>
# (default: dev). All wrap scripts/ that source scripts/lib/azd-common.sh.
# ---------------------------------------------------------------------------

smoke:       ## Sprint 28 A5: post-deploy smoke (curl /healthz, /readyz, /tenant/config) — ENV=dev
	scripts/azd-smoke.sh $(ENV)

rotate-key:  ## Sprint 28: rotate a tenant API key via tools-job — ENV=dev TENANT=test-corp
	@if [ -z "$(TENANT)" ]; then echo "TENANT=<slug> required" >&2; exit 2; fi
	scripts/azd-job.sh $(ENV) smoke_setup.py -- --regenerate-key --tenant-slug "$(TENANT)"

logs:        ## Tail container logs — ENV=dev SERVICE=api SINCE=15m
	@if [ -z "$(SERVICE)" ]; then echo "SERVICE=<api|worker|mqtt> required" >&2; exit 2; fi
	scripts/azd-logs.sh $(ENV) $(SERVICE) $(if $(SINCE),--since $(SINCE),)

doctor:      ## Sprint 28 F3: aggregate health check for an env — ENV=dev
	scripts/azd-doctor.sh $(ENV)

# ---------------------------------------------------------------------------
# Sprint 58 Phase B — demo tenant composer.
# ``make demo-tenant`` runs scripts/seed_demo_tenant.py end-to-end against
# the local stack (docker compose). Re-runs are idempotent. Use
# ``DEMO_KEEP_KEY=1 make demo-tenant`` to reuse $TAGPULSE_API_KEY instead
# of rotating, or ``DEMO_SKIP_BACKFILL=1`` to skip the historical replay.
# ---------------------------------------------------------------------------

demo-tenant: ## Sprint 58: seed the WM Distribution Center demo tenant (idempotent)
	python scripts/seed_demo_tenant.py

demo-tenant-reset: ## Sprint 58: delete the demo tenant + recipient (local dev only)
	python scripts/reset_demo_tenant.py

help:        ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | sort | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'
