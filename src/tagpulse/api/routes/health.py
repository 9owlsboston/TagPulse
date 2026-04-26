"""Health check routes — liveness, readiness, and detailed status."""

import asyncio
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tagpulse.repositories.timescaledb.session import async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Liveness probe — fast, no dependency checks."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe — checks DB and critical components."""
    checks = await _run_checks(request)
    all_up = all(c["status"] == "up" for c in checks.values())
    status = "healthy" if all_up else "degraded"
    code = 200 if all_up else 503
    return JSONResponse({"status": status, "checks": checks}, status_code=code)


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

    return JSONResponse({
        "status": "healthy" if all_up else "degraded",
        "checks": checks,
        "event_bus": {
            "queue_sizes": queue_sizes,
            "running": event_bus._running if event_bus else False,
        },
        "usage_meter": {"status": meter_status},
    })


async def _run_checks(request: Request) -> dict[str, dict[str, object]]:
    """Run all health checks and return results."""
    checks: dict[str, dict[str, object]] = {}

    # Database check
    checks["database"] = await _check_database()

    # MQTT check
    from tagpulse.core.config import settings
    checks["mqtt"] = await _check_mqtt(settings.mqtt_broker_host, settings.mqtt_broker_port)

    # EventBus check
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus:
        checks["event_bus"] = {"status": "up" if event_bus._running else "down"}
    else:
        checks["event_bus"] = {"status": "down"}

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
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3.0
        )
        writer.close()
        await writer.wait_closed()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "up", "latency_ms": latency_ms}
    except (TimeoutError, OSError) as exc:
        return {"status": "down", "error": str(exc)}
