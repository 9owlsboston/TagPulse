"""Sprint 17a — zone.entered / zone.exited rule evaluation + DwellWorker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import RuleResponse

# ---- Fakes (mirrored from test_phase_e_inventory_rules) ----


class _FakeMeter:
    def __init__(self) -> None:
        self.records: list[tuple[Any, str, str]] = []

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        self.records.append((tenant_id, dimension, unit))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


class _FakeRulesService:
    def __init__(self, rules: list[RuleResponse]) -> None:
        self._rules = rules
        self.alerts: list[dict[str, Any]] = []

    async def get_active_rules_by_condition_type(
        self, tenant_id: UUID, condition_type: str
    ) -> list[RuleResponse]:
        return [r for r in self._rules if r.condition_type == condition_type]

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
            "triggered_at": datetime.now(UTC),
        }
        self.alerts.append(record)

        class _A:
            def __init__(self, r: dict[str, Any]) -> None:
                self.id = r["id"]
                self.severity = r["severity"]
                self.message = r["message"]
                self.triggered_at = r["triggered_at"]

        return _A(record)


class _DummyCtx:
    async def __aenter__(self) -> Any:
        return _Session()

    async def __aexit__(self, *args: Any) -> None:
        return None


class _Session:
    async def commit(self) -> None:
        return None


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


# ---- zone.entered / zone.exited ----


@pytest.fixture(autouse=True)
def _clear_cooldowns() -> None:
    from tagpulse.rules.evaluator import _RULE_COOLDOWN_UNTIL

    _RULE_COOLDOWN_UNTIL.clear()


@pytest.mark.asyncio
async def test_zone_entered_fires_for_matching_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    zone = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.entered",
        condition_config={"zone_id": str(zone), "cooldown_s": 0},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    meter = _FakeMeter()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )
    event = Event(
        id=uuid4(),
        topic=Topic.SUBJECT_ZONE_CHANGED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(tenant),
            "subject_kind": "asset",
            "subject_id": str(uuid4()),
            "from_zone_id": None,
            "to_zone_id": str(zone),
        },
    )
    await evaluator.on_subject_zone_changed(event)
    assert len(fake_rules.alerts) == 1
    assert any(t == Topic.ALERT_TRIGGERED for t, _ in bus.published)


@pytest.mark.asyncio
async def test_zone_entered_respects_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    zone = uuid4()
    subject = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.entered",
        condition_config={"zone_id": str(zone), "cooldown_s": 60},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    def _event() -> Event:
        return Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=datetime.now(UTC),
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",
                "subject_id": str(subject),
                "from_zone_id": None,
                "to_zone_id": str(zone),
            },
        )

    await evaluator.on_subject_zone_changed(_event())
    await evaluator.on_subject_zone_changed(_event())
    assert len(fake_rules.alerts) == 1


@pytest.mark.asyncio
async def test_zone_exited_fires_for_from_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    zone = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.exited",
        condition_config={"zone_id": str(zone), "cooldown_s": 0},
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    event = Event(
        id=uuid4(),
        topic=Topic.SUBJECT_ZONE_CHANGED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(tenant),
            "subject_kind": "asset",
            "subject_id": str(uuid4()),
            "from_zone_id": str(zone),
            "to_zone_id": None,
        },
    )
    await evaluator.on_subject_zone_changed(event)
    assert len(fake_rules.alerts) == 1


@pytest.mark.asyncio
async def test_zone_subject_kinds_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    zone = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.entered",
        condition_config={
            "zone_id": str(zone),
            "subject_kinds": ["stock_item"],
            "cooldown_s": 0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    # asset event — should NOT match because subject_kinds restricts to stock_item.
    await evaluator.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=datetime.now(UTC),
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",
                "subject_id": str(uuid4()),
                "from_zone_id": None,
                "to_zone_id": str(zone),
            },
        )
    )
    assert fake_rules.alerts == []


# ---- DwellTracker / DwellWorker ----


@pytest.mark.asyncio
async def test_dwell_tracker_records_state() -> None:
    from tagpulse.workers.dwell_worker import DwellTracker

    tracker = DwellTracker()
    tenant = uuid4()
    subject = uuid4()
    zone = uuid4()
    await tracker.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=datetime.now(UTC),
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",
                "subject_id": str(subject),
                "to_zone_id": str(zone),
                "from_zone_id": None,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    )
    snapshot = tracker.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0][0] == tenant
    assert snapshot[0][2] == str(zone)


@pytest.mark.asyncio
async def test_dwell_worker_fires_when_threshold_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.workers.dwell_worker import DwellTracker, DwellWorker

    tenant = uuid4()
    zone = uuid4()
    subject = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.dwell_exceeded",
        condition_config={
            "zone_id": str(zone),
            "threshold_minutes": 5,
            "cooldown_s": 0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.workers.dwell_worker.RulesService", lambda session: fake_rules)
    tracker = DwellTracker()
    # Subject entered zone 10 minutes ago — well past the 5-minute threshold.
    entered_at = datetime.now(UTC) - timedelta(minutes=10)
    await tracker.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=entered_at,
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",
                "subject_id": str(subject),
                "to_zone_id": str(zone),
                "from_zone_id": None,
                "timestamp": entered_at.isoformat(),
            },
        )
    )
    worker = DwellWorker(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
        tracker=tracker,
    )
    await worker.run_once()
    assert len(fake_rules.alerts) == 1


@pytest.mark.asyncio
async def test_dwell_worker_skips_when_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.workers.dwell_worker import DwellTracker, DwellWorker

    tenant = uuid4()
    zone = uuid4()
    subject = uuid4()
    rule = _make_rule(
        tenant,
        condition_type="zone.dwell_exceeded",
        condition_config={
            "zone_id": str(zone),
            "threshold_minutes": 60,
            "cooldown_s": 0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.workers.dwell_worker.RulesService", lambda session: fake_rules)
    tracker = DwellTracker()
    entered_at = datetime.now(UTC) - timedelta(minutes=5)
    await tracker.on_subject_zone_changed(
        Event(
            id=uuid4(),
            topic=Topic.SUBJECT_ZONE_CHANGED,
            timestamp=entered_at,
            payload={
                "tenant_id": str(tenant),
                "subject_kind": "asset",
                "subject_id": str(subject),
                "to_zone_id": str(zone),
                "from_zone_id": None,
                "timestamp": entered_at.isoformat(),
            },
        )
    )
    worker = DwellWorker(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
        tracker=tracker,
    )
    await worker.run_once()
    assert fake_rules.alerts == []
