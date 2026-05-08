"""Health check routes — liveness, readiness, and detailed status.

Sprint 22 A5/A6 (cloud readiness, ADR-016):

* ``/health`` — kept for backward compatibility, aliases liveness.
* ``/health/live`` — k8s-convention liveness probe; fast, no deps.
* ``/health/ready`` — readiness probe; DB + MQTT + alembic_version
  parity. Body now includes a ``config`` dict so cloud operators can
  verify env-var wiring without shelling into the container.
* ``/health/detail`` — long-running diagnostics with queue sizes.
"""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tagpulse.core.config import settings
from tagpulse.core.migration_check import (
    expected_head_revision,
    fetch_db_revision,
)
from tagpulse.repositories.timescaledb.session import async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Liveness probe — fast, no dependency checks.

    Kept for backward compatibility (Sprint 22 A6 + earlier callers). The
    SPA contract is the richer ``/health/live`` shape.
    """
    return {"status": "ok"}


@router.get("/health/live")
async def liveness_alias() -> JSONResponse:
    """Liveness probe — Sprint 25 A1 SPA contract.

    Returns ``{"status": "alive", "version": "<sha>", "build_time": "<iso8601>"}``
    in <50ms with no DB / MQTT / migration touches. ``Cache-Control: no-store``
    is set explicitly so the SWA edge / browser back-cache never memoize the
    body — a stale "alive" response would defeat the SPA's startup gate when
    the api goes down. Build identity comes from ``Settings.build_version`` and
    ``Settings.build_time`` (Dockerfile-baked at image build time).
    """
    return JSONResponse(
        {
            "status": "alive",
            "version": settings.build_version,
            "build_time": settings.build_time,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe — checks DB, MQTT, and migration version."""
    checks = await _run_checks(request)
    all_up = all(c["status"] == "up" for c in checks.values())
    status = "healthy" if all_up else "degraded"
    code = 200 if all_up else 503
    return JSONResponse(
        {
            "status": status,
            "checks": checks,
            "config": _config_snapshot(),
        },
        status_code=code,
    )


@router.get("/health/detail")
async def detail(request: Request) -> JSONResponse:
    """Detailed health — includes queue sizes and component stats."""
    checks = await _run_checks(request)
    all_up = all(c["status"] == "up" for c in checks.values())

    # EventBus details
    event_bus = getattr(request.app.state, "event_bus", None)
    queue_sizes: dict[str, int] = {}
    if event_bus:
        for topic, queue in event_bus._queues.items():
            queue_sizes[topic.value] = queue.qsize()

    # UsageMeter details
    usage_meter = getattr(request.app.state, "usage_meter", None)
    meter_status = "unknown"
    if usage_meter:
        meter_status = "up" if usage_meter._task and not usage_meter._task.done() else "down"

    return JSONResponse(
        {
            "status": "healthy" if all_up else "degraded",
            "checks": checks,
            "config": _config_snapshot(),
            "event_bus": {
                "queue_sizes": queue_sizes,
                "running": event_bus._running if event_bus else False,
            },
            "usage_meter": {"status": meter_status},
        }
    )


def _config_snapshot() -> dict[str, Any]:
    """Surface the cloud-relevant config knobs in /health/ready.

    Per ADR-016 §7 + Sprint 22 A5: operators verifying env-var wiring
    after a fresh deploy should be able to ``curl /health/ready`` and
    see exactly what the running process believes.
    """
    return {
        "environment": settings.environment,
        "max_ingest_payload_bytes": settings.max_ingest_payload_bytes,
        "ingest_clock_enforce": settings.ingest_clock_enforce,
        "geofence_evaluation_enabled": settings.geofence_evaluation_enabled,
        "rate_limit_enabled": settings.rate_limit_enabled,
        "strict_migration_check": settings.strict_migration_check,
        # Sprint 24 A2: surface CORS allow-list so operators can verify the
        # SWA hostname is wired into the deployed api revision without
        # shelling into the container. The most common post-deploy failure
        # mode is "SPA loads but every fetch is blocked by CORS".
        "cors": {
            "allow_origins": [o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        },
    }


async def _run_checks(request: Request) -> dict[str, dict[str, object]]:
    """Run all health checks and return results."""
    checks: dict[str, dict[str, object]] = {}

    # Database check
    checks["database"] = await _check_database()

    # MQTT check
    checks["mqtt"] = await _check_mqtt(settings.mqtt_broker_host, settings.mqtt_broker_port)

    # EventBus check
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        checks["event_bus"] = {"status": "up" if event_bus._running else "down"}
    else:
        checks["event_bus"] = {"status": "down"}

    # Migration version check (Sprint 22 A7).
    checks["migrations"] = await _check_migrations()

    return checks


async def _check_database() -> dict[str, object]:
    """Check database connectivity with SELECT 1."""
    start = time.monotonic()
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "up", "latency_ms": latency_ms}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


async def _check_mqtt(host: str, port: int) -> dict[str, object]:
    """Check MQTT broker connectivity via TCP connect."""
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        writer.close()
        await writer.wait_closed()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "up", "latency_ms": latency_ms}
    except (TimeoutError, OSError) as exc:
        return {"status": "down", "error": str(exc)}


async def _check_migrations() -> dict[str, object]:
    """Compare DB ``alembic_version`` against the code's head revision."""
    expected = expected_head_revision()
    try:
        actual = await fetch_db_revision(async_session_factory)
    except Exception as exc:
        return {"status": "down", "error": str(exc), "expected": expected}
    if actual is None:
        return {"status": "down", "expected": expected, "actual": None}
    if actual != expected:
        return {
            "status": "down",
            "expected": expected,
            "actual": actual,
            "error": "schema drift — DB revision != code head",
        }
    return {"status": "up", "revision": actual}
