"""Unit tests for the consolidation worker's custody-event logic (Sprint 71)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.services.consolidation import AssetStateSnapshot
from tagpulse.workers.consolidation_worker import AssetConsolidationWorker

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


def _snap(asset_id, frame: str, **kw: Any) -> AssetStateSnapshot:
    base: dict[str, Any] = dict(
        asset_id=asset_id,
        time=NOW,
        frame=frame,
        zone_id=None,
        site_id=None,
        lat=None,
        lon=None,
        x=None,
        y=None,
        temperature_c=None,
        humidity_pct=None,
        sample_count=1,
        tag_count=1,
        confidence=0.5,
    )
    base.update(kw)
    return AssetStateSnapshot(**base)


def _worker(bus: _FakeBus) -> AssetConsolidationWorker:
    # session_factory is unused by _emit_custody; pass a dummy.
    return AssetConsolidationWorker(session_factory=object(), event_bus=bus)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_first_seen_emits_no_custody_event() -> None:
    bus = _FakeBus()
    worker = _worker(bus)
    asset = uuid4()
    tenant = uuid4()
    await worker._emit_custody(tenant, [_snap(asset, "reader")])
    assert bus.published == []


@pytest.mark.asyncio
async def test_frame_change_emits_custody_event() -> None:
    bus = _FakeBus()
    worker = _worker(bus)
    asset, tenant = uuid4(), uuid4()
    zone = uuid4()
    await worker._emit_custody(tenant, [_snap(asset, "reader", zone_id=zone)])
    await worker._emit_custody(tenant, [_snap(asset, "geo")])
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.ASSET_CUSTODY_CHANGED
    assert event.payload["from_frame"] == "reader"
    assert event.payload["to_frame"] == "geo"
    assert event.payload["asset_id"] == str(asset)


@pytest.mark.asyncio
async def test_unchanged_frame_emits_nothing() -> None:
    bus = _FakeBus()
    worker = _worker(bus)
    asset, tenant = uuid4(), uuid4()
    await worker._emit_custody(tenant, [_snap(asset, "reader")])
    await worker._emit_custody(tenant, [_snap(asset, "reader")])
    assert bus.published == []
