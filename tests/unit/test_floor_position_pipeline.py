"""Unit tests for the floor-position estimation pipeline (Sprint 66, Phase 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tagpulse.services.floor_position_estimator import (
    FloorObservation,
    FloorPositionEstimatorService,
)
from tagpulse.services.positioning import PositionStrategy
from tagpulse.workers.floor_position_worker import FloorPositionWorker

NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)


class _FakeObservations:
    def __init__(self, by_tenant: dict[UUID, list[FloorObservation]]) -> None:
        self._by_tenant = by_tenant
        self.calls: list[tuple[UUID, datetime]] = []

    async def recent_observations(self, tenant_id: UUID, *, since: datetime):
        self.calls.append((tenant_id, since))
        return self._by_tenant.get(tenant_id, [])


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert_computed(
        self, tenant_id, asset_id, *, site_id, recorded_at, x, y, confidence, metadata=None
    ) -> None:
        self.rows.append(
            {
                "tenant_id": tenant_id,
                "asset_id": asset_id,
                "site_id": site_id,
                "recorded_at": recorded_at,
                "x": x,
                "y": y,
                "confidence": confidence,
                "metadata": metadata,
            }
        )


class _FakeStrategies:
    def __init__(self, items: list[tuple[UUID, PositionStrategy]]) -> None:
        self._items = items

    async def tenants_with_strategy(self):
        return self._items


def _obs(site, asset, x, y, rssi, age_s=0.0) -> FloorObservation:
    return FloorObservation(
        site_id=site,
        asset_id=asset,
        antenna_id=uuid4(),
        x=x,
        y=y,
        rssi=rssi,
        cnt=1,
        ts=NOW - timedelta(seconds=age_s),
    )


@pytest.mark.asyncio
async def test_run_once_writes_one_fix_per_asset() -> None:
    tenant, site, asset = uuid4(), uuid4(), uuid4()
    obs = _FakeObservations(
        {
            tenant: [
                _obs(site, asset, 10.0, 10.0, -55.0),
                _obs(site, asset, 40.0, 10.0, -60.0),
                _obs(site, asset, 25.0, 35.0, -61.0),
            ]
        }
    )
    writer = _FakeWriter()
    strategies = _FakeStrategies([(tenant, PositionStrategy(rssi_floor_dbm=-127.0))])
    svc = FloorPositionEstimatorService(obs, writer, strategies)

    written = await svc.run_once(NOW)

    assert written == 1
    assert len(writer.rows) == 1
    row = writer.rows[0]
    assert row["tenant_id"] == tenant
    assert row["asset_id"] == asset
    assert row["site_id"] == site
    assert row["recorded_at"] == NOW
    assert row["metadata"]["antennas"] == 3
    # Centroid inside the contributing antennas' bbox.
    assert 10.0 <= row["x"] <= 40.0
    assert 10.0 <= row["y"] <= 35.0


@pytest.mark.asyncio
async def test_run_once_separates_assets_and_sites() -> None:
    tenant = uuid4()
    site_a, site_b = uuid4(), uuid4()
    asset_1, asset_2 = uuid4(), uuid4()
    obs = _FakeObservations(
        {
            tenant: [
                _obs(site_a, asset_1, 1.0, 1.0, -55.0),
                _obs(site_a, asset_1, 3.0, 1.0, -58.0),
                _obs(site_b, asset_2, 5.0, 5.0, -55.0),
                _obs(site_b, asset_2, 7.0, 5.0, -58.0),
            ]
        }
    )
    writer = _FakeWriter()
    strategies = _FakeStrategies([(tenant, PositionStrategy(rssi_floor_dbm=-127.0))])
    svc = FloorPositionEstimatorService(obs, writer, strategies)

    written = await svc.run_once(NOW)

    assert written == 2
    pairs = {(r["asset_id"], r["site_id"]) for r in writer.rows}
    assert pairs == {(asset_1, site_a), (asset_2, site_b)}


@pytest.mark.asyncio
async def test_run_once_respects_lookback_window_in_query() -> None:
    tenant = uuid4()
    obs = _FakeObservations({tenant: []})
    strategies = _FakeStrategies([(tenant, PositionStrategy(lookback_s=12.0))])
    svc = FloorPositionEstimatorService(obs, _FakeWriter(), strategies)

    await svc.run_once(NOW)

    assert obs.calls == [(tenant, NOW - timedelta(seconds=12.0))]


@pytest.mark.asyncio
async def test_run_once_skips_when_below_min_antennas() -> None:
    tenant, site, asset = uuid4(), uuid4(), uuid4()
    obs = _FakeObservations({tenant: [_obs(site, asset, 1.0, 1.0, -55.0)]})
    writer = _FakeWriter()
    strategies = _FakeStrategies([(tenant, PositionStrategy(min_antennas=2))])
    svc = FloorPositionEstimatorService(obs, writer, strategies)

    written = await svc.run_once(NOW)

    assert written == 0
    assert writer.rows == []


@pytest.mark.asyncio
async def test_run_once_no_opted_in_tenants_is_a_noop() -> None:
    writer = _FakeWriter()
    svc = FloorPositionEstimatorService(_FakeObservations({}), writer, _FakeStrategies([]))
    assert await svc.run_once(NOW) == 0
    assert writer.rows == []


@pytest.mark.asyncio
async def test_worker_run_once_delegates_to_service() -> None:
    tenant, site, asset = uuid4(), uuid4(), uuid4()
    obs = _FakeObservations(
        {tenant: [_obs(site, asset, 1.0, 1.0, -55.0), _obs(site, asset, 3.0, 1.0, -58.0)]}
    )
    writer = _FakeWriter()
    strategies = _FakeStrategies([(tenant, PositionStrategy(rssi_floor_dbm=-127.0))])
    svc = FloorPositionEstimatorService(obs, writer, strategies)
    worker = FloorPositionWorker(svc, interval_s=0.01)

    # Pass the fixed NOW so the observations (pinned to NOW) fall inside the
    # estimator's lookback window deterministically — the bare run_once()
    # uses real wall-clock time, which excludes the fixed-NOW observations
    # whenever the suite runs more than `lookback_s` after NOW.
    assert await worker.run_once(NOW) == 1


@pytest.mark.asyncio
async def test_worker_start_stop_is_clean() -> None:
    svc = FloorPositionEstimatorService(_FakeObservations({}), _FakeWriter(), _FakeStrategies([]))
    worker = FloorPositionWorker(svc, interval_s=0.01)
    await worker.start()
    await worker.stop()
