"""Unit tests for ``OverlappingZonesProcessor`` (Sprint 41 Phase D2).

DB-mocked tests that exercise the runtime wrapper around
:func:`tagpulse.signaling.overlapping_zones.aggregate`:

* ``run_once_for_rule`` queries zones + reads, runs aggregate(),
  publishes one ``SIGNALING_ATTRIBUTION_SETTLED`` event per attribution.
* Event payload conforms to the ADR-021 §"``signaling.attribution_settled``
  payload — coordinate-system-agnostic" pin: zone_id + site_id +
  confidence + window bounds + provenance, no lat/lon/x/y fields.
* The processor catches per-rule errors so a single bad rule cannot
  break the dispatcher tick (covered indirectly via the dispatcher
  test).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import RuleResponse
from tagpulse.signaling.overlapping_zones import OverlappingZonesProcessor


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


class _ScalarsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)

    def all(self) -> list[Any]:
        return self._rows


class _RowsResult:
    """Mimic ``result.all()`` over named tuples / objects."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeZoneRow:
    """ZoneModel row stand-in — only the fields ``_load_zones`` reads."""

    def __init__(
        self,
        *,
        zone_id: UUID,
        site_id: UUID,
        kind: str,
        created_at: datetime,
        fixed_reader_ids: list[str] | None = None,
        polygon_geojson: dict | None = None,
        bbox_min_lat: float | None = None,
        bbox_max_lat: float | None = None,
        bbox_min_lon: float | None = None,
        bbox_max_lon: float | None = None,
    ) -> None:
        self.id = zone_id
        self.site_id = site_id
        self.kind = kind
        self.created_at = created_at
        self.fixed_reader_ids = fixed_reader_ids
        self.polygon_geojson = polygon_geojson
        self.bbox_min_lat = bbox_min_lat
        self.bbox_max_lat = bbox_max_lat
        self.bbox_min_lon = bbox_min_lon
        self.bbox_max_lon = bbox_max_lon


class _FakeReadRow:
    """``_READS_SQL`` row stand-in. Attributes mirror the SELECT list."""

    def __init__(
        self,
        *,
        asset_id: UUID,
        reader_id: UUID,
        timestamp: datetime,
        signal_strength: float | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        self.asset_id = asset_id
        self.reader_id = reader_id
        self.timestamp = timestamp
        self.signal_strength = signal_strength
        self.latitude = latitude
        self.longitude = longitude


class _FakeSession:
    """Routes the two ``execute()`` calls to canned zone + read results.

    ``_load_zones`` issues a ``select(ZoneModel)``-style statement;
    ``_load_reads`` issues a ``text()``-bound SQL statement. We
    distinguish by whether the statement is the textual ``_READS_SQL``
    (its ``compile()`` returns a string containing ``asset_tag_bindings``).
    """

    def __init__(self, *, zone_rows: list[_FakeZoneRow], read_rows: list[_FakeReadRow]) -> None:
        self._zone_rows = zone_rows
        self._read_rows = read_rows
        self.execute_calls: list[Any] = []

    async def execute(self, statement: Any, params: dict | None = None) -> Any:
        self.execute_calls.append((statement, params))
        # ``_READS_SQL`` is a sqlalchemy ``TextClause``; ``select`` from
        # the zone loader is a ``Select`` — distinguish by class name to
        # avoid importing the sqlalchemy internals.
        cls_name = type(statement).__name__
        if cls_name == "TextClause":
            return _RowsResult(self._read_rows)
        return _ScalarsResult(self._zone_rows)


def _make_rule(
    *,
    tenant_id: UUID | None = None,
    processor_config: dict | None = None,
    confidence_threshold: Decimal | None = None,
) -> RuleResponse:
    now = datetime.now(UTC)
    cfg: dict[str, Any] = {"cadence_minutes": 1}
    if processor_config is not None:
        cfg["processor_config"] = processor_config
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        name="overlap-rule",
        description=None,
        condition_type="signaling.location.periodic",
        condition_config=cfg,
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
        event_type="location",
        trigger="periodic",
        processor="overlapping_zones",
        confidence_threshold=confidence_threshold or Decimal("0.0"),
    )


# ---------------------------------------------------------------------------
# Empty / no-op paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_for_rule_no_zones_returns_zero() -> None:
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    rule = _make_rule()
    session = _FakeSession(zone_rows=[], read_rows=[])

    emitted = await processor.run_once_for_rule(session, rule)  # type: ignore[arg-type]

    assert emitted == 0
    assert bus.published == []
    # Only the zones query should have run; reads query is skipped.
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_run_once_for_rule_no_reads_returns_zero() -> None:
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    rule = _make_rule()
    zone_id = uuid4()
    site_id = uuid4()
    zones = [
        _FakeZoneRow(
            zone_id=zone_id,
            site_id=site_id,
            kind="reader_bound",
            created_at=datetime.now(UTC),
            fixed_reader_ids=[str(uuid4())],
        )
    ]
    session = _FakeSession(zone_rows=zones, read_rows=[])

    emitted = await processor.run_once_for_rule(session, rule)  # type: ignore[arg-type]

    assert emitted == 0
    assert bus.published == []


# ---------------------------------------------------------------------------
# Happy path — one attribution → one event with correct payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_for_rule_emits_one_event_per_attribution() -> None:
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    tenant_id = uuid4()
    rule = _make_rule(tenant_id=tenant_id)

    asset_id = uuid4()
    reader_id = uuid4()
    zone_id = uuid4()
    site_id = uuid4()
    now = datetime.now(UTC)

    zones = [
        _FakeZoneRow(
            zone_id=zone_id,
            site_id=site_id,
            kind="reader_bound",
            created_at=now - timedelta(days=1),
            fixed_reader_ids=[str(reader_id)],
        )
    ]
    reads = [
        _FakeReadRow(
            asset_id=asset_id,
            reader_id=reader_id,
            timestamp=now,
            signal_strength=-55.0,
        ),
        _FakeReadRow(
            asset_id=asset_id,
            reader_id=reader_id,
            timestamp=now - timedelta(seconds=30),
            signal_strength=-60.0,
        ),
    ]
    session = _FakeSession(zone_rows=zones, read_rows=reads)

    emitted = await processor.run_once_for_rule(session, rule, now=now)  # type: ignore[arg-type]

    assert emitted == 1
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.SIGNALING_ATTRIBUTION_SETTLED
    assert event.topic == Topic.SIGNALING_ATTRIBUTION_SETTLED
    payload = event.payload
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["asset_id"] == str(asset_id)
    assert payload["zone_id"] == str(zone_id)
    assert payload["site_id"] == str(site_id)
    assert payload["confidence"] == 1.0
    assert payload["contributing_reads"] == 2
    assert payload["contributing_readers"] == [str(reader_id)]
    assert payload["rule_id"] == str(rule.id)
    # Window bounds must be ISO strings spanning the configured window.
    assert payload["window_end"] == now.isoformat()
    expected_start = (now - timedelta(seconds=60)).isoformat()
    assert payload["window_start"] == expected_start


@pytest.mark.asyncio
async def test_run_once_for_rule_payload_has_no_coordinates() -> None:
    """ADR-021 v2 pin: ``signaling.attribution_settled`` payload is
    coordinate-system-agnostic. The processor MUST NOT emit lat/lon
    or x/y on the event — coordinate fields belong to the (separate)
    trilateration table in Sprint 45."""
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    rule = _make_rule()
    asset, reader = uuid4(), uuid4()
    zone_id, site_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    zones = [
        _FakeZoneRow(
            zone_id=zone_id,
            site_id=site_id,
            kind="reader_bound",
            created_at=now - timedelta(days=1),
            fixed_reader_ids=[str(reader)],
        )
    ]
    reads = [
        _FakeReadRow(
            asset_id=asset,
            reader_id=reader,
            timestamp=now,
            latitude=12.345,  # present on the SQL row but must not leak
            longitude=67.890,
        )
    ]
    session = _FakeSession(zone_rows=zones, read_rows=reads)

    await processor.run_once_for_rule(session, rule, now=now)  # type: ignore[arg-type]

    payload = bus.published[0][1].payload
    forbidden = {"latitude", "longitude", "lat", "lon", "x", "y"}
    assert not (set(payload.keys()) & forbidden), (
        f"payload leaked coordinate fields: {payload.keys() & forbidden}"
    )


@pytest.mark.asyncio
async def test_run_once_for_rule_overlap_emits_two_events() -> None:
    """A genuine overlap (one asset in two zones with equal evidence)
    must produce two attribution_settled events for that asset."""
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    rule = _make_rule(processor_config={"zone_bleed_filter": False})
    asset = uuid4()
    reader_a, reader_b = uuid4(), uuid4()
    zone_a, zone_b = uuid4(), uuid4()
    site = uuid4()
    now = datetime.now(UTC)
    zones = [
        _FakeZoneRow(
            zone_id=zone_a,
            site_id=site,
            kind="reader_bound",
            created_at=now - timedelta(days=2),
            fixed_reader_ids=[str(reader_a)],
        ),
        _FakeZoneRow(
            zone_id=zone_b,
            site_id=site,
            kind="reader_bound",
            created_at=now - timedelta(days=1),
            fixed_reader_ids=[str(reader_b)],
        ),
    ]
    reads = [
        _FakeReadRow(asset_id=asset, reader_id=reader_a, timestamp=now),
        _FakeReadRow(asset_id=asset, reader_id=reader_b, timestamp=now),
    ]
    session = _FakeSession(zone_rows=zones, read_rows=reads)

    emitted = await processor.run_once_for_rule(session, rule, now=now)  # type: ignore[arg-type]

    assert emitted == 2
    confidences = sorted(ev.payload["confidence"] for _, ev in bus.published)
    assert confidences == [0.5, 0.5]


@pytest.mark.asyncio
async def test_run_once_for_rule_uses_config_window() -> None:
    """An aggregation_window_s=30 rule must run with a 30-second window
    (plus skew tolerance) — read at 25s ago in, read at 60s ago out."""
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    rule = _make_rule(
        processor_config={
            "aggregation_window_s": 30,
            "time_error_filter": 0,
        }
    )
    asset, reader = uuid4(), uuid4()
    zone_id, site_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    zones = [
        _FakeZoneRow(
            zone_id=zone_id,
            site_id=site_id,
            kind="reader_bound",
            created_at=now - timedelta(days=1),
            fixed_reader_ids=[str(reader)],
        )
    ]
    reads = [
        _FakeReadRow(asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=10)),
        _FakeReadRow(asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=25)),
        _FakeReadRow(asset_id=asset, reader_id=reader, timestamp=now - timedelta(seconds=60)),
    ]
    session = _FakeSession(zone_rows=zones, read_rows=reads)

    emitted = await processor.run_once_for_rule(session, rule, now=now)  # type: ignore[arg-type]

    assert emitted == 1
    payload = bus.published[0][1].payload
    assert payload["contributing_reads"] == 2  # third read was out-of-window
    expected_start = (now - timedelta(seconds=30)).isoformat()
    assert payload["window_start"] == expected_start


@pytest.mark.asyncio
async def test_run_once_for_rule_reads_query_uses_tenant_and_window() -> None:
    """The ``_READS_SQL`` execution must be parameterised by the rule's
    tenant_id and the configured window bounds (with skew tolerance)."""
    bus = _FakeBus()
    processor = OverlappingZonesProcessor(bus)  # type: ignore[arg-type]
    tenant_id = uuid4()
    rule = _make_rule(
        tenant_id=tenant_id,
        processor_config={"aggregation_window_s": 60, "time_error_filter": 5},
    )
    reader = uuid4()
    zone_id, site_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    zones = [
        _FakeZoneRow(
            zone_id=zone_id,
            site_id=site_id,
            kind="reader_bound",
            created_at=now - timedelta(days=1),
            fixed_reader_ids=[str(reader)],
        )
    ]
    session = _FakeSession(zone_rows=zones, read_rows=[])

    await processor.run_once_for_rule(session, rule, now=now)  # type: ignore[arg-type]

    # First call = zones query; second = reads query.
    assert len(session.execute_calls) == 2
    _, reads_params = session.execute_calls[1]
    assert reads_params is not None
    assert reads_params["tenant_id"] == tenant_id
    assert reads_params["window_end"] == now
    assert reads_params["window_start"] == now - timedelta(seconds=65)
