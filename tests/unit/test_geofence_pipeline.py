"""Sprint 17a — geofence pipeline integration tests.

Mirrors design §9 ("ingest a stream of GPS-tagged reads → expected zone
transitions → expected alerts") without requiring a live database. We wire
the real ``IngestionService._eval_geofence_for_subject`` against a fake zone
repo (returning canned candidates) and a fake event bus, then feed the
emitted events into the real ``RuleEvaluator`` against fake rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.ingestion.service import (
    _LAST_GEOFENCE_BY_ASSET,
    IngestionService,
)
from tagpulse.models.rule_schemas import RuleResponse
from tagpulse.models.schemas import Location, TagReadCreate, ZoneResponse

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeZoneRepo:
    """Returns a fixed zone list filtered by bbox; mirrors the real repo."""

    def __init__(self, zones: list[ZoneResponse]) -> None:
        self._zones = zones
        self.calls = 0

    async def find_geofence_candidates(
        self, tenant_id: UUID, lat: float, lon: float
    ) -> list[ZoneResponse]:
        self.calls += 1
        return [
            z
            for z in self._zones
            if z.tenant_id == tenant_id
            and z.bbox_min_lat is not None
            and z.bbox_min_lat <= lat <= (z.bbox_max_lat or lat)
            and z.bbox_min_lon is not None
            and z.bbox_min_lon <= lon <= (z.bbox_max_lon or lon)
        ]


class _CapturingBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.events.append(event)


class _FakeMeter:
    def record(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeRulesService:
    def __init__(self, rules: list[RuleResponse]) -> None:
        self._rules = rules
        self.alerts: list[dict[str, Any]] = []

    async def get_active_rules_by_condition_type(
        self, tenant_id: UUID, condition_type: str
    ) -> list[RuleResponse]:
        return [
            r for r in self._rules if r.condition_type == condition_type
        ]

    async def create_alert(
        self,
        tenant_id: UUID,
        rule_id: UUID,
        *,
        device_id: UUID | None,
        severity: str,
        message: str,
        context: dict[str, Any],
    ) -> Any:
        record = {
            "id": uuid4(),
            "rule_id": rule_id,
            "severity": severity,
            "message": message,
            "context": context,
        }
        self.alerts.append(record)

        class _A:
            def __init__(self, r: dict[str, Any]) -> None:
                self.id = r["id"]
                self.severity = r["severity"]
                self.message = r["message"]
                self.triggered_at = datetime.now(UTC)

        return _A(record)


class _DummyCtx:
    async def __aenter__(self) -> Any:
        return _Session()

    async def __aexit__(self, *args: Any) -> None:
        return None


class _Session:
    async def commit(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zone(
    tenant_id: UUID, *, name: str = "z1"
) -> ZoneResponse:
    """Square geofence covering (47.60, -122.34) — (47.61, -122.33)."""
    return ZoneResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        site_id=uuid4(),
        name=name,
        kind="geofence",
        fixed_reader_ids=None,
        polygon_geojson={
            "type": "Polygon",
            "coordinates": [
                [
                    [-122.34, 47.60],
                    [-122.33, 47.60],
                    [-122.33, 47.61],
                    [-122.34, 47.61],
                    [-122.34, 47.60],
                ]
            ],
        },
        bbox_min_lat=47.60,
        bbox_max_lat=47.61,
        bbox_min_lon=-122.34,
        bbox_max_lon=-122.33,
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_rule(
    tenant_id: UUID,
    *,
    condition_type: str,
    condition_config: dict[str, Any],
) -> RuleResponse:
    now = datetime.now(UTC)
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        name="r",
        description=None,
        condition_type=condition_type,
        condition_config=condition_config,
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def _make_read(*, lat: float, lon: float) -> TagReadCreate:
    return TagReadCreate(
        device_id=uuid4(),
        tag_id="TAG0001",
        timestamp=datetime.now(UTC),
        location=Location(latitude=lat, longitude=lon, source="gps"),
    )


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    from tagpulse.rules.evaluator import _RULE_COOLDOWN_UNTIL

    _LAST_GEOFENCE_BY_ASSET.clear()
    _RULE_COOLDOWN_UNTIL.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geofence_emits_event_only_on_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream of reads: outside → inside → inside → outside.

    Expect 2 ``subject.zone_changed`` emits (in, out), not one per read.
    Per design §4.1.
    """
    monkeypatch.setattr(
        "tagpulse.ingestion.service.settings.geofence_evaluation_enabled",
        True,
    )
    tenant = uuid4()
    asset = uuid4()
    zone = _make_zone(tenant)
    bus = _CapturingBus()
    repo = _FakeZoneRepo([zone])
    svc = IngestionService(
        repo=None,  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        zone_repo=repo,  # type: ignore[arg-type]
    )

    # 1) Outside (seed — no emit, just primes the cache).
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.50, lon=-122.40),
        tag_read_id=uuid4(),
    )
    # 2) Inside — transition None→zone, emit.
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.605, lon=-122.335),
        tag_read_id=uuid4(),
    )
    # 3) Still inside — no emit.
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.606, lon=-122.336),
        tag_read_id=uuid4(),
    )
    # 4) Outside again — transition zone→None, emit.
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.50, lon=-122.40),
        tag_read_id=uuid4(),
    )

    geofence_events = [
        e
        for e in bus.events
        if e.payload.get("zone_kind") == "geofence"
    ]
    assert len(geofence_events) == 2
    assert geofence_events[0].payload["from_zone_id"] is None
    assert geofence_events[0].payload["to_zone_id"] == str(zone.id)
    assert geofence_events[1].payload["from_zone_id"] == str(zone.id)
    assert geofence_events[1].payload["to_zone_id"] is None


@pytest.mark.asyncio
async def test_geofence_pipeline_to_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: geofence transition → ``zone.entered`` rule → alert."""
    from tagpulse.rules.evaluator import RuleEvaluator

    monkeypatch.setattr(
        "tagpulse.ingestion.service.settings.geofence_evaluation_enabled",
        True,
    )
    tenant = uuid4()
    asset = uuid4()
    zone = _make_zone(tenant)

    rule = _make_rule(
        tenant,
        condition_type="zone.entered",
        condition_config={"zone_id": str(zone.id), "cooldown_s": 0},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr(
        "tagpulse.rules.evaluator.RulesService",
        lambda session: fake_rules,
    )

    bus = _CapturingBus()
    repo = _FakeZoneRepo([zone])
    svc = IngestionService(
        repo=None,  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        zone_repo=repo,  # type: ignore[arg-type]
    )
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    # Seed (outside).
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.50, lon=-122.40),
        tag_read_id=uuid4(),
    )
    # Cross into the zone.
    await svc._eval_geofence_for_subject(
        tenant_id=tenant,
        subject_kind="asset",
        subject_id=asset,
        read=_make_read(lat=47.605, lon=-122.335),
        tag_read_id=uuid4(),
    )

    # Replay the geofence event into the evaluator.
    transitions = [
        e
        for e in bus.events
        if e.payload.get("zone_kind") == "geofence"
    ]
    assert len(transitions) == 1
    await evaluator.on_subject_zone_changed(transitions[0])

    assert len(fake_rules.alerts) == 1
    assert fake_rules.alerts[0]["severity"] == "info"
