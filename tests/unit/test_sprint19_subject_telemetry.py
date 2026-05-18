"""Sprint 19 unit tests — multi-subject telemetry ingest + APIs.

Covers:

* Subject fan-out from ``IngestionService._mirror_tag_borne_sensors``
  for a tenant opted into ``["device", "asset", "lot"]``.
* The MQTT subject-topic parser ``_parse_subject_topic``.
* The 301 redirect for the legacy ``/telemetry-models/{device_type}``
  path.
* ``InventoryService.get_lot`` / ``AssetService.get_asset`` embedding
  of ``latest_telemetry`` only when the tenant has opted in.

Repository protocols are exercised via lightweight in-memory fakes so
the tests run without a database — same pattern as
``test_telemetry_service.py`` / ``test_ingestion_service.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.core.telemetry_caches import SUBJECT_KINDS_CACHE
from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.ingestion.mqtt_subscriber import _parse_subject_topic
from tagpulse.ingestion.service import (
    _BINDING_BY_VALUE,
    IngestionService,
)
from tagpulse.models.schemas import (
    AssetResponse,
    AssetTagBindingResponse,
    Identity,
    LatestTelemetryEntry,
    StockItemResponse,
    TagReadCreate,
    TagReadResponse,
    TelemetryReadingResponse,
)

# -- Shared fakes --


class FakeTagReadRepo:
    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagReadResponse:
        return TagReadResponse(
            id=uuid4(),
            device_id=read.device_id,
            tag_id=read.tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            created_at=datetime.now(UTC),
        )

    async def record_rejection(self, tenant_id: UUID, read: TagReadCreate, reason: str) -> None:
        return None


class FakeTelemetryReadingsRepo:
    """Records every ``insert`` so tests can assert subject fan-out."""

    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []
        self._latest: list[LatestTelemetryEntry] = []

    async def insert(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        timestamp: datetime,
        metric_name: str,
        metric_value: float,
        device_id: UUID | None = None,
        unit: str | None = None,
        source: str = "device",
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryReadingResponse:
        self.inserts.append(
            {
                "tenant_id": tenant_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "timestamp": timestamp,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "device_id": device_id,
                "unit": unit,
                "source": source,
                "metadata": metadata,
            }
        )
        return TelemetryReadingResponse(
            id=uuid4(),
            subject_kind=subject_kind,  # type: ignore[arg-type]
            subject_id=subject_id,
            device_id=device_id,
            timestamp=timestamp,
            metric_name=metric_name,
            metric_value=metric_value,
            unit=unit,
            source=source,  # type: ignore[arg-type]
            metadata=metadata,
        )

    async def latest_per_metric(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        limit: int = 5,
    ) -> list[LatestTelemetryEntry]:
        return list(self._latest)


class FakeBindingRepo:
    def __init__(self, asset_id: UUID | None) -> None:
        self._asset_id = asset_id

    async def get_active_by_value(
        self, tenant_id: UUID, binding_value: str
    ) -> AssetTagBindingResponse | None:
        if self._asset_id is None:
            return None
        return AssetTagBindingResponse(
            id=uuid4(),
            tenant_id=tenant_id,
            asset_id=self._asset_id,
            binding_kind="epc",
            binding_value=binding_value,
            bound_at=datetime.now(UTC),
            unbound_at=None,
            metadata=None,
        )


class FakeStockRepo:
    def __init__(self, stock_id: UUID | None, lot_id: UUID | None) -> None:
        self._stock_id = stock_id
        self._lot_id = lot_id
        self._tenant: UUID | None = None

    async def get_active_by_binding(
        self, tenant_id: UUID, binding_kind: str, binding_value: str
    ) -> StockItemResponse | None:
        if self._stock_id is None:
            return None
        return StockItemResponse(
            id=self._stock_id,
            tenant_id=tenant_id,
            product_id=uuid4(),
            lot_id=self._lot_id,
            parent_stock_item_id=None,
            binding_value=binding_value,
            binding_kind=binding_kind,
            state="in_stock",
            current_zone_id=None,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            consumed_at=None,
            metadata=None,
        )


class FakeTenantRepo:
    def __init__(self, kinds: list[str]) -> None:
        self._kinds = kinds

    async def get_tracking_modes(self, tenant_id: UUID) -> list[str]:
        return ["asset", "inventory"]

    async def get_telemetry_subject_kinds(self, tenant_id: UUID) -> list[str]:
        return list(self._kinds)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    _BINDING_BY_VALUE.clear()
    SUBJECT_KINDS_CACHE.clear()


# -- _mirror_tag_borne_sensors fan-out --


@pytest.mark.asyncio
async def test_subject_fanout_writes_one_row_per_opted_in_subject() -> None:
    """tenant has opted into device+asset+lot; tag resolves to all three.

    Expect: 3 inserts on the readings repo (asset, stock_item, lot) —
    device-row write goes through the legacy telemetry_service path
    which is None in this fixture, so we only count the fan-out side.
    """
    asset_id = uuid4()
    stock_id = uuid4()
    lot_id = uuid4()
    tenant_id = uuid4()

    readings_repo = FakeTelemetryReadingsRepo()

    service = IngestionService(
        repo=FakeTagReadRepo(),
        event_bus=AsyncEventBus(capacity=100),
        binding_repo=FakeBindingRepo(asset_id=asset_id),  # type: ignore[arg-type]
        stock_repo=FakeStockRepo(stock_id=stock_id, lot_id=lot_id),  # type: ignore[arg-type]
        tenant_repo=FakeTenantRepo(  # type: ignore[arg-type]
            kinds=["device", "asset", "lot", "stock_item"]
        ),
        telemetry_readings_repo=readings_repo,  # type: ignore[arg-type]
    )

    read = TagReadCreate(
        device_id=uuid4(),
        tag_id="EPC-001",
        timestamp=datetime.now(UTC),
        signal_strength=-50.0,
        tag_data={"temperature_c": 4.2},
        identity=Identity(epc="urn:epc:id:sgtin:00000.1.1", epc_hex=None),
    )

    await service._mirror_tag_borne_sensors(tenant_id, read, tag_read_id=uuid4())

    kinds = sorted(i["subject_kind"] for i in readings_repo.inserts)
    assert kinds == ["asset", "lot", "stock_item"]
    assert all(i["metric_name"] == "temperature_c" for i in readings_repo.inserts)
    assert all(i["source"] == "tag" for i in readings_repo.inserts)
    asset_row = next(i for i in readings_repo.inserts if i["subject_kind"] == "asset")
    assert asset_row["subject_id"] == asset_id
    assert asset_row["device_id"] == read.device_id


@pytest.mark.asyncio
async def test_subject_fanout_skips_when_tenant_not_opted_in() -> None:
    """default ``["device"]`` only → zero subject inserts."""
    readings_repo = FakeTelemetryReadingsRepo()
    service = IngestionService(
        repo=FakeTagReadRepo(),
        event_bus=AsyncEventBus(capacity=100),
        binding_repo=FakeBindingRepo(asset_id=uuid4()),  # type: ignore[arg-type]
        stock_repo=FakeStockRepo(stock_id=uuid4(), lot_id=uuid4()),  # type: ignore[arg-type]
        tenant_repo=FakeTenantRepo(kinds=["device"]),  # type: ignore[arg-type]
        telemetry_readings_repo=readings_repo,  # type: ignore[arg-type]
    )
    read = TagReadCreate(
        device_id=uuid4(),
        tag_id="EPC-001",
        timestamp=datetime.now(UTC),
        signal_strength=-50.0,
        tag_data={"temperature_c": 4.2},
    )
    await service._mirror_tag_borne_sensors(uuid4(), read, tag_read_id=uuid4())
    assert readings_repo.inserts == []


@pytest.mark.asyncio
async def test_subject_fanout_unresolved_logs_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """opted-in but no binding/stock → no inserts, INFO log emitted."""
    readings_repo = FakeTelemetryReadingsRepo()
    service = IngestionService(
        repo=FakeTagReadRepo(),
        event_bus=AsyncEventBus(capacity=100),
        binding_repo=FakeBindingRepo(asset_id=None),  # type: ignore[arg-type]
        stock_repo=FakeStockRepo(stock_id=None, lot_id=None),  # type: ignore[arg-type]
        tenant_repo=FakeTenantRepo(kinds=["device", "asset"]),  # type: ignore[arg-type]
        telemetry_readings_repo=readings_repo,  # type: ignore[arg-type]
    )
    read = TagReadCreate(
        device_id=uuid4(),
        tag_id="EPC-XYZ",
        timestamp=datetime.now(UTC),
        signal_strength=-50.0,
        tag_data={"humidity": 50.0},
    )
    import logging

    with caplog.at_level(logging.INFO, logger="tagpulse.ingestion.service"):
        await service._mirror_tag_borne_sensors(uuid4(), read, tag_read_id=uuid4())
    assert readings_repo.inserts == []
    assert any("telemetry.subject_unresolved" in r.message for r in caplog.records)


# -- MQTT subject topic parser --


def test_parse_subject_topic_valid() -> None:
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    parsed = _parse_subject_topic(f"tenants/{tid}/subjects/asset/{sid}/telemetry")
    assert parsed == (tid, "asset", sid)


def test_parse_subject_topic_unknown_kind() -> None:
    tid = uuid.uuid4()
    sid = uuid.uuid4()
    parsed = _parse_subject_topic(f"tenants/{tid}/subjects/widget/{sid}/telemetry")
    assert parsed == (None, None, None)


def test_parse_subject_topic_legacy_topic_returns_none() -> None:
    tid = uuid.uuid4()
    did = uuid.uuid4()
    parsed = _parse_subject_topic(f"tenants/{tid}/devices/{did}/telemetry")
    assert parsed == (None, None, None)


def test_parse_subject_topic_bad_uuid() -> None:
    parsed = _parse_subject_topic("tenants/not-a-uuid/subjects/asset/also-not/telemetry")
    assert parsed == (None, None, None)


# -- AssetService.get_asset latest_telemetry embed --


class _FakeAssetRepo:
    def __init__(self, asset: AssetResponse | None) -> None:
        self._asset = asset

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        return self._asset


@pytest.mark.asyncio
async def test_get_asset_embeds_latest_when_opted_in() -> None:
    from tagpulse.api.services.asset_service import AssetService
    from tagpulse.core.audit import AuditLogger

    asset_id = uuid4()
    tenant_id = uuid4()
    asset = AssetResponse(
        id=asset_id,
        tenant_id=tenant_id,
        external_ref=None,
        name="A",
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    readings_repo = FakeTelemetryReadingsRepo()
    readings_repo._latest = [
        LatestTelemetryEntry(
            metric_name="temperature_c",
            metric_value=5.1,
            unit="°C",
            timestamp=datetime.now(UTC),
            source="tag",
        )
    ]

    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=None,  # type: ignore[arg-type]
        audit=AuditLogger(session=None),  # type: ignore[arg-type]
        telemetry_readings_repo=readings_repo,  # type: ignore[arg-type]
        tenant_repo=FakeTenantRepo(kinds=["device", "asset"]),  # type: ignore[arg-type]
    )
    fetched = await svc.get_asset(tenant_id, asset_id, with_latest_telemetry=True)
    assert fetched is not None
    assert fetched.latest_telemetry is not None
    assert len(fetched.latest_telemetry) == 1
    assert fetched.latest_telemetry[0].metric_name == "temperature_c"


@pytest.mark.asyncio
async def test_get_asset_skips_latest_when_not_opted_in() -> None:
    from tagpulse.api.services.asset_service import AssetService
    from tagpulse.core.audit import AuditLogger

    asset_id = uuid4()
    tenant_id = uuid4()
    asset = AssetResponse(
        id=asset_id,
        tenant_id=tenant_id,
        external_ref=None,
        name="A",
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=None,  # type: ignore[arg-type]
        audit=AuditLogger(session=None),  # type: ignore[arg-type]
        telemetry_readings_repo=FakeTelemetryReadingsRepo(),  # type: ignore[arg-type]
        tenant_repo=FakeTenantRepo(kinds=["device"]),  # type: ignore[arg-type]
    )
    fetched = await svc.get_asset(tenant_id, asset_id, with_latest_telemetry=True)
    assert fetched is not None
    assert fetched.latest_telemetry is None


# -- /telemetry-models/{device_type} 410 Gone (Sprint 21 sunset) --


@pytest.mark.asyncio
async def test_telemetry_models_legacy_path_no_longer_routed() -> None:
    """``GET /telemetry-models/{device_type}`` was finally removed in
    Sprint 28 (H6).

    Deprecation history:
    - Sprint 19: introduced 301 redirect to ``/device/{device_type}``.
    - Sprint 21: redirect replaced with 410 Gone tombstone.
    - Sprint 28: tombstone dropped entirely. The single-segment path
      ``/telemetry-models/{x}`` is still registered for DELETE and
      PATCH (as ``{model_id}``), so a GET hits FastAPI's method
      router and returns 405 Method Not Allowed — either way the
      legacy GET-by-device_type contract is gone.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from tagpulse.api.routes import telemetry_models as tm_routes
    from tagpulse.core.user_auth import (
        AuthenticatedUser,
        get_current_user,
    )

    app = FastAPI()
    app.include_router(tm_routes.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=uuid4(),
            tenant_id=uuid4(),
            tenant_name="T",
            tenant_slug="t",
            role="viewer",
        )

    app.dependency_overrides[get_current_user] = _user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/telemetry-models/temperature-sensor",
            follow_redirects=False,
        )
    assert r.status_code in (404, 405)
