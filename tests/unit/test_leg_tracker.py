"""Unit tests for AssetLegTracker custody-event routing (Sprint 72, ADR-034)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.workers.leg_tracker import AssetLegTracker

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


def _event(from_frame: str, to_frame: str, *, tenant, asset, from_zone=None, zone=None) -> Event:
    return Event(
        id=uuid4(),
        topic=Topic.ASSET_CUSTODY_CHANGED,
        timestamp=NOW,
        payload={
            "tenant_id": str(tenant),
            "asset_id": str(asset),
            "from_frame": from_frame,
            "to_frame": to_frame,
            "from_zone_id": str(from_zone) if from_zone else None,
            "from_site_id": None,
            "zone_id": str(zone) if zone else None,
            "site_id": None,
            "timestamp": NOW.isoformat(),
        },
    )


def _tracker(monkeypatch):  # type: ignore[no-untyped-def]
    t = AssetLegTracker(session_factory=object())  # type: ignore[arg-type]
    calls: list[tuple] = []

    async def fake_open(tenant_id, asset_id, *, origin_zone_id, origin_site_id, departed_at):  # type: ignore[no-untyped-def]
        calls.append(("open", tenant_id, asset_id, origin_zone_id))

    async def fake_close(tenant_id, asset_id, *, dest_zone_id, dest_site_id, arrived_at):  # type: ignore[no-untyped-def]
        calls.append(("close", tenant_id, asset_id, dest_zone_id))

    monkeypatch.setattr(t, "_open_leg", fake_open)
    monkeypatch.setattr(t, "_close_leg", fake_close)
    return t, calls


@pytest.mark.asyncio
async def test_reader_to_geo_opens_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    t, calls = _tracker(monkeypatch)
    tenant, asset, zone = uuid4(), uuid4(), uuid4()
    await t.on_custody_changed(_event("reader", "geo", tenant=tenant, asset=asset, from_zone=zone))
    assert calls == [("open", tenant, asset, zone)]


@pytest.mark.asyncio
async def test_geo_to_reader_closes_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    t, calls = _tracker(monkeypatch)
    tenant, asset, dest = uuid4(), uuid4(), uuid4()
    await t.on_custody_changed(_event("geo", "reader", tenant=tenant, asset=asset, zone=dest))
    assert calls == [("close", tenant, asset, dest)]


@pytest.mark.asyncio
async def test_geo_to_none_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    t, calls = _tracker(monkeypatch)
    await t.on_custody_changed(_event("geo", "none", tenant=uuid4(), asset=uuid4()))
    assert calls == []


@pytest.mark.asyncio
async def test_reader_to_floor_closes_any_open_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    # facility → facility (no geo) still routes to close (arrival safety net).
    t, calls = _tracker(monkeypatch)
    tenant, asset, dest = uuid4(), uuid4(), uuid4()
    await t.on_custody_changed(_event("reader", "floor", tenant=tenant, asset=asset, zone=dest))
    assert calls == [("close", tenant, asset, dest)]


@pytest.mark.asyncio
async def test_none_to_geo_does_not_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # geo arrival from an ambiguous frame is not a facility departure → no open.
    t, calls = _tracker(monkeypatch)
    await t.on_custody_changed(_event("none", "geo", tenant=uuid4(), asset=uuid4()))
    assert calls == []


@pytest.mark.asyncio
async def test_missing_ids_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    t, calls = _tracker(monkeypatch)
    ev = Event(
        id=uuid4(),
        topic=Topic.ASSET_CUSTODY_CHANGED,
        timestamp=NOW,
        payload={"to_frame": "geo", "from_frame": "reader"},
    )
    await t.on_custody_changed(ev)
    assert calls == []
