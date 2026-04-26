"""Prometheus /metrics endpoint — serves OpenTelemetry metrics in Prometheus format."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Expose metrics in Prometheus text format for scraping."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
