# syntax=docker/dockerfile:1.7
#
# Sprint 22 B1 — multi-target image. Three production images share one
# build stage (``base``) and differ only in CMD:
#
#   --target api          → uvicorn (HTTP only; set WORKERS_INLINE=false)
#   --target worker       → uvicorn (workers + MQTT subscriber; default
#                           WORKERS_INLINE=true; HTTP exposes /health/live
#                           for k8s probes only)
#   --target migrations   → one-shot ``alembic upgrade head`` for the
#                           pre-rollout migration job (B2)
#
# Build matrix: see .github/workflows/build-and-push.yml (B3).

FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
# Install with [azure] extra so App Insights export works in ACA (Sprint 22 C3).
# The extra is a no-op for non-Azure deploys (env var APPLICATIONINSIGHTS_CONNECTION_STRING
# unset → soft-import branch in core/telemetry.py is skipped).
RUN pip install --no-cache-dir ".[azure]"

FROM python:3.12-slim AS base
WORKDIR /app
RUN useradd --create-home appuser && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=build /usr/local/bin/alembic /usr/local/bin/alembic
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .

LABEL org.opencontainers.image.source="https://github.com/9owlsboston/TagPulse"

# Sprint 25 A1 — bake the build identity into the image so /health/live can
# surface ``version`` + ``build_time`` to the SPA without a runtime lookup.
# build-and-push.yml passes BUILD_VERSION=${{github.sha}} and a UTC ISO-8601
# BUILD_TIME; local ``docker build`` without --build-arg keeps the dev defaults.
ARG BUILD_VERSION=dev
ARG BUILD_TIME=unknown
ENV BUILD_VERSION=${BUILD_VERSION} \
    BUILD_TIME=${BUILD_TIME}

USER appuser


# -----------------------------------------------------------------------------
# api — HTTP only
# -----------------------------------------------------------------------------
FROM base AS api
LABEL org.opencontainers.image.description="TagPulse API (HTTP only)"
ENV WORKERS_INLINE=false
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -f http://localhost:8000/health/live || exit 1
CMD ["uvicorn", "tagpulse.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--log-level", "info"]


# -----------------------------------------------------------------------------
# worker — MQTT subscriber + inventory/dwell/alert/analytics/webhook workers.
# Runs the same FastAPI app with workers enabled; HTTP is exposed for liveness
# probes only (Helm chart does not Service-route to it).
# -----------------------------------------------------------------------------
FROM base AS worker
LABEL org.opencontainers.image.description="TagPulse Worker (MQTT + background workers)"
ENV WORKERS_INLINE=true
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -f http://localhost:8000/health/live || exit 1
CMD ["uvicorn", "tagpulse.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--log-level", "info"]


# -----------------------------------------------------------------------------
# migrations — one-shot. Runs to completion; container exits 0/1.
# Used by deploy/common/migrations-job.yaml (B2) and the Bicep deploy (C2).
# -----------------------------------------------------------------------------
FROM base AS migrations
LABEL org.opencontainers.image.description="TagPulse Migrations (alembic upgrade head)"
ENTRYPOINT ["alembic", "-c", "/app/alembic.ini"]
CMD ["upgrade", "head"]


# -----------------------------------------------------------------------------
# default — preserve back-compat for ``docker build .`` (no --target).
# -----------------------------------------------------------------------------
FROM api AS default

