"""Tests for asset current-location, path, and zone-occupancy queries.

Sprint 15 — exercises :class:`TimescaleAssetLocationRepository` indirectly via
:class:`AssetService` with stubbed repos. The view/SQL itself is integration
material; here we lock in the service contract + route plumbing semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.asset_service import AssetService
from tagpulse.models.schemas import (
    AssetCurrentLocation,
    AssetInZoneSummary,
    AssetPathPoint,
    AssetResponse,
)


def _asset(tenant_id: UUID) -> AssetResponse:
    return AssetResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        external_ref=None,
        name="Pallet-9",
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class _FakeAssetRepo:
    def __init__(self, asset: AssetResponse | None) -> None:
        self._asset = asset

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        return self._asset


class _FakeAssetLocationRepo:
    def __init__(self) -> None:
        self.current: AssetCurrentLocation | None = None
        self.list_current: list[AssetCurrentLocation] = []
        self.path: list[AssetPathPoint] = []
        self.in_zone: list[AssetInZoneSummary] = []
        self.path_calls: list[dict[str, Any]] = []
        self.in_zone_calls: list[dict[str, Any]] = []

    async def get_current_location(
        self, tenant_id: UUID, asset_id: UUID
    ) -> AssetCurrentLocation | None:
        return self.current

    async def list_current_locations(
        self, tenant_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> Sequence[AssetCurrentLocation]:
        return self.list_current

    async def get_asset_path(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int = 1000,
    ) -> Sequence[AssetPathPoint]:
        self.path_calls.append(
            {"since": since, "until": until, "limit": limit, "asset_id": asset_id}
        )
        return self.path

    async def get_assets_in_zone(
        self,
        tenant_id: UUID,
        zone_id: UUID,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[AssetInZoneSummary]:
        self.in_zone_calls.append({"zone_id": zone_id, "limit": limit, "offset": offset})
        return self.in_zone


class _NullAudit:
    async def log(self, **kwargs: Any) -> None:  # pragma: no cover
        return None


def _make_service(
    *, asset: AssetResponse | None = None
) -> tuple[AssetService, _FakeAssetLocationRepo]:
    loc_repo = _FakeAssetLocationRepo()
    svc = AssetService(
        asset_repo=_FakeAssetRepo(asset),  # type: ignore[arg-type]
        binding_repo=None,  # type: ignore[arg-type]
        audit=_NullAudit(),  # type: ignore[arg-type]
        asset_location_repo=loc_repo,  # type: ignore[arg-type]
    )
    return svc, loc_repo


@pytest.mark.asyncio
async def test_get_current_location_returns_repo_value() -> None:
    tenant = uuid4()
    asset = _asset(tenant)
    svc, repo = _make_service(asset=asset)
    repo.current = AssetCurrentLocation(
        asset_id=asset.id,
        recorded_at=datetime.now(UTC),
        latitude=37.0,
        longitude=-122.0,
        accuracy_meters=2.5,
        device_id=uuid4(),
        latest_position_source="rfid",
    )

    got = await svc.get_current_location(tenant, asset.id)

    assert got is not None
    assert got.latest_position_source == "rfid"
    assert got.asset_id == asset.id


@pytest.mark.asyncio
async def test_get_current_location_returns_none_when_no_fix() -> None:
    tenant = uuid4()
    svc, repo = _make_service(asset=_asset(tenant))
    repo.current = None

    got = await svc.get_current_location(tenant, uuid4())

    assert got is None


@pytest.mark.asyncio
async def test_get_asset_path_passes_window_to_repo() -> None:
    tenant = uuid4()
    asset = _asset(tenant)
    svc, repo = _make_service(asset=asset)
    since = datetime.now(UTC) - timedelta(hours=1)
    until = datetime.now(UTC)
    repo.path = [
        AssetPathPoint(
            recorded_at=since + timedelta(minutes=5),
            latitude=37.1,
            longitude=-122.1,
            accuracy_meters=1.0,
            source="rfid",
            device_id=uuid4(),
            tag_read_id=uuid4(),
        ),
        AssetPathPoint(
            recorded_at=since + timedelta(minutes=10),
            latitude=37.2,
            longitude=-122.2,
            accuracy_meters=10.0,
            source="samsara",
            external_id=uuid4(),
        ),
    ]

    got = await svc.get_asset_path(tenant, asset.id, since=since, until=until, limit=50)

    assert len(got) == 2
    assert {p.source for p in got} == {"rfid", "samsara"}
    assert repo.path_calls[0] == {
        "since": since,
        "until": until,
        "limit": 50,
        "asset_id": asset.id,
    }


def test_path_sql_references_existing_tag_reads_columns() -> None:
    """Regression: ``_PATH_SQL`` must use ``tr.device_id`` (the actual column
    on ``tag_reads``), not ``tr.reader_id`` which has never existed and caused
    a 500 on every ``GET /assets/{id}/path`` call.
    """
    from tagpulse.repositories.timescaledb.asset_location import (
        TimescaleAssetLocationRepository,
    )

    sql = str(TimescaleAssetLocationRepository._PATH_SQL)
    assert "tr.reader_id" not in sql, "tag_reads has no reader_id column"
    assert "tr.device_id" in sql


def test_epc_bindings_match_uri_or_hex() -> None:
    """ADR-033: ``binding_kind='epc'`` must resolve against EITHER the decoded
    URI (``tr.epc``) or the raw hex (``tr.epc_hex``), so a hex binding (what WM
    gives operators) resolves location/path/zone surfaces.
    """
    from tagpulse.repositories.timescaledb.asset_location import (
        TimescaleAssetLocationRepository,
    )
    from tagpulse.signaling.overlapping_zones import OverlappingZonesProcessor

    for label, sql in (
        ("_PATH_SQL", str(TimescaleAssetLocationRepository._PATH_SQL)),
        (
            "_ASSETS_IN_READER_BOUND_ZONE",
            str(TimescaleAssetLocationRepository._ASSETS_IN_READER_BOUND_ZONE),
        ),
        ("overlapping _READS_SQL", str(OverlappingZonesProcessor._READS_SQL)),
    ):
        assert "tr.epc_hex = b.binding_value" in sql, f"{label} missing epc_hex match"
        assert "tr.epc = b.binding_value" in sql, f"{label} missing epc URI match"


@pytest.mark.asyncio
async def test_get_asset_path_raises_without_repo() -> None:
    svc = AssetService(
        asset_repo=_FakeAssetRepo(None),  # type: ignore[arg-type]
        binding_repo=None,  # type: ignore[arg-type]
        audit=_NullAudit(),  # type: ignore[arg-type]
        asset_location_repo=None,
    )
    with pytest.raises(RuntimeError, match="asset_location_repo not configured"):
        await svc.get_asset_path(
            uuid4(),
            uuid4(),
            since=datetime.now(UTC) - timedelta(hours=1),
            until=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_get_assets_in_zone_passes_pagination() -> None:
    tenant = uuid4()
    zone_id = uuid4()
    svc, repo = _make_service()
    repo.in_zone = [
        AssetInZoneSummary(
            asset_id=uuid4(),
            name="Pallet-A",
            last_seen_at=datetime.now(UTC),
            binding_value="E280-AAAA",
            binding_kind="epc",
        )
    ]

    got = await svc.get_assets_in_zone(tenant, zone_id, limit=50, offset=10)

    assert len(got) == 1
    assert got[0].name == "Pallet-A"
    assert repo.in_zone_calls[0] == {
        "zone_id": zone_id,
        "limit": 50,
        "offset": 10,
    }


@pytest.mark.asyncio
async def test_list_current_locations_returns_list() -> None:
    tenant = uuid4()
    svc, repo = _make_service()
    repo.list_current = [
        AssetCurrentLocation(
            asset_id=uuid4(),
            recorded_at=datetime.now(UTC),
            latitude=1.0,
            longitude=2.0,
            accuracy_meters=None,
            device_id=None,
            latest_position_source="manual",
        )
    ]

    got = await svc.list_current_locations(tenant)

    assert isinstance(got, list)
    assert got[0].latest_position_source == "manual"
