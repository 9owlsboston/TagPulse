"""Unit tests for the read-side telemetry routes (Sprint 14).

Calls the route coroutines directly with a stub service so the parameter
wiring (query params → service kwargs) is exercised without booting the app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.routes.query import query_tag_reads
from tagpulse.api.routes.telemetry import (
    list_telemetry,
    list_telemetry_quarantine,
)
from tagpulse.core.tenant_auth import Tenant
from tagpulse.models.schemas import (
    TagReadResponse,
    TelemetryQuarantineResponse,
    TelemetryResponse,
)


def _tenant() -> Tenant:
    return Tenant(id=uuid4(), name="Acme", slug="acme", plan="free")


class _StubTelemetryService:
    def __init__(self) -> None:
        self.query_kwargs: dict[str, Any] | None = None
        self.list_quarantine_kwargs: dict[str, Any] | None = None

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryResponse]:
        self.query_kwargs = {
            "tenant_id": tenant_id,
            "device_id": device_id,
            "metric_name": metric_name,
            "start": start,
            "end": end,
            "limit": limit,
        }
        return [
            TelemetryResponse(
                id=uuid4(),
                device_id=device_id or uuid4(),
                timestamp=datetime.now(UTC),
                metric_name=metric_name or "temperature",
                metric_value=22.5,
                unit="C",
                metadata=None,
            )
        ]

    async def list_quarantine(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        reason: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TelemetryQuarantineResponse]:
        self.list_quarantine_kwargs = {
            "tenant_id": tenant_id,
            "device_id": device_id,
            "reason": reason,
            "limit": limit,
            "offset": offset,
        }
        return [
            TelemetryQuarantineResponse(
                id=uuid4(),
                device_id=device_id or uuid4(),
                received_at=datetime.now(UTC),
                metric_name="temperature",
                metric_value=999.0,
                raw_payload={"metric_value": 999.0},
                reason=reason or "out_of_range",
            )
        ]


class _StubQueryService:
    def __init__(self) -> None:
        self.query_kwargs: dict[str, Any] | None = None

    async def query_tag_reads(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        tag_id: str | None = None,
        tag_q: str | None = None,
        epc_q: str | None = None,
        asset_q: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        has_location: bool | None = None,
        epc_scheme: str | None = None,
        sort: str | None = None,
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        self.query_kwargs = {
            "tenant_id": tenant_id,
            "device_id": device_id,
            "tag_id": tag_id,
            "tag_q": tag_q,
            "epc_q": epc_q,
            "asset_q": asset_q,
            "start": start,
            "end": end,
            "has_location": has_location,
            "epc_scheme": epc_scheme,
            "sort": sort,
            "order": order,
            "limit": limit,
            "offset": offset,
        }
        return []


@pytest.mark.asyncio
async def test_list_telemetry_forwards_query_params() -> None:
    tenant = _tenant()
    service = _StubTelemetryService()
    device = uuid4()
    start = datetime.now(UTC) - timedelta(hours=2)
    end = datetime.now(UTC)

    rows = await list_telemetry(
        device_id=device,
        metric_name="temperature",
        start=start,
        end=end,
        limit=50,
        tenant=tenant,
        service=service,  # type: ignore[arg-type]
    )

    assert len(rows) == 1
    assert service.query_kwargs == {
        "tenant_id": tenant.id,
        "device_id": device,
        "metric_name": "temperature",
        "start": start,
        "end": end,
        "limit": 50,
    }


@pytest.mark.asyncio
async def test_list_telemetry_quarantine_forwards_query_params() -> None:
    tenant = _tenant()
    service = _StubTelemetryService()
    device = uuid4()

    rows = await list_telemetry_quarantine(
        device_id=device,
        reason="out_of_range",
        limit=25,
        offset=10,
        tenant=tenant,
        service=service,  # type: ignore[arg-type]
    )

    assert len(rows) == 1
    assert rows[0].reason == "out_of_range"
    assert service.list_quarantine_kwargs == {
        "tenant_id": tenant.id,
        "device_id": device,
        "reason": "out_of_range",
        "limit": 25,
        "offset": 10,
    }


@pytest.mark.asyncio
async def test_list_telemetry_defaults_apply_when_no_filters() -> None:
    tenant = _tenant()
    service = _StubTelemetryService()

    await list_telemetry(
        device_id=None,
        metric_name=None,
        start=None,
        end=None,
        limit=100,
        tenant=tenant,
        service=service,  # type: ignore[arg-type]
    )

    assert service.query_kwargs is not None
    assert service.query_kwargs["device_id"] is None
    assert service.query_kwargs["metric_name"] is None
    assert service.query_kwargs["limit"] == 100


@pytest.mark.asyncio
async def test_query_tag_reads_forwards_has_location_and_epc_scheme() -> None:
    tenant = _tenant()
    service = _StubQueryService()

    await query_tag_reads(
        device_id=None,
        tag_id=None,
        start=None,
        end=None,
        has_location=True,
        epc_scheme="sgtin-96",
        limit=100,
        offset=0,
        tenant=tenant,
        service=service,  # type: ignore[arg-type]
    )

    assert service.query_kwargs is not None
    assert service.query_kwargs["has_location"] is True
    assert service.query_kwargs["epc_scheme"] == "sgtin-96"
    assert service.query_kwargs["tenant_id"] == tenant.id
