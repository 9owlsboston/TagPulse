"""Unit tests for ``RuleEvaluator.on_attribution_settled`` (Sprint 41 Phase D2).

Covers the consumer of ``Topic.SIGNALING_ATTRIBUTION_SETTLED`` events
emitted by the OverlappingZones processor. The evaluator matches
``signaling.<event_type>.on_inference`` rules, applies the
``min_confidence`` threshold + cooldown, then fires an alert and
publishes ``Topic.ALERT_TRIGGERED``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import RuleResponse

# ---------------------------------------------------------------------------
# Fakes (mirrored from tests/unit/test_zone_rules_and_dwell.py)
# ---------------------------------------------------------------------------


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


def _make_on_inference_rule(
    *,
    tenant_id: UUID,
    event_type: str = "location",
    min_confidence: float = 0.0,
    cooldown_s: int = 60,
) -> RuleResponse:
    now = datetime.now(UTC)
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        name="on-inference-rule",
        description=None,
        condition_type=f"signaling.{event_type}.on_inference",
        condition_config={
            "min_confidence": min_confidence,
            "cooldown_s": cooldown_s,
        },
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        trigger="on_inference",
    )


def _attribution_event(
    *,
    tenant_id: UUID,
    asset_id: UUID,
    zone_id: UUID,
    site_id: UUID,
    confidence: float = 1.0,
    rule_id: UUID | None = None,
) -> Event:
    now = datetime.now(UTC)
    return Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=now,
        payload={
            "tenant_id": str(tenant_id),
            "asset_id": str(asset_id),
            "zone_id": str(zone_id),
            "site_id": str(site_id),
            "confidence": confidence,
            "window_start": now.isoformat(),
            "window_end": now.isoformat(),
            "contributing_reads": 2,
            "contributing_readers": [str(uuid4())],
            "rule_id": str(rule_id or uuid4()),
        },
    )


@pytest.fixture(autouse=True)
def _clear_cooldowns() -> None:
    from tagpulse.rules.evaluator import _RULE_COOLDOWN_UNTIL

    _RULE_COOLDOWN_UNTIL.clear()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_fires_alert_when_confidence_meets_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant, min_confidence=0.5)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    meter = _FakeMeter()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )
    event = _attribution_event(
        tenant_id=tenant,
        asset_id=uuid4(),
        zone_id=uuid4(),
        site_id=uuid4(),
        confidence=0.8,
    )

    await evaluator.on_attribution_settled(event)

    assert len(fake_rules.alerts) == 1
    alert = fake_rules.alerts[0]
    assert alert["severity"] == "info"
    assert "confidence=0.80" in alert["message"]
    assert any(t == Topic.ALERT_TRIGGERED for t, _ in bus.published)
    # Both meter dims recorded.
    dims = [d for _, d, _ in meter.records]
    assert "rule_evaluations" in dims
    assert "alerts_fired" in dims


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_skipped_when_below_min_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant, min_confidence=0.9)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    meter = _FakeMeter()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )
    event = _attribution_event(
        tenant_id=tenant,
        asset_id=uuid4(),
        zone_id=uuid4(),
        site_id=uuid4(),
        confidence=0.5,
    )

    await evaluator.on_attribution_settled(event)

    assert fake_rules.alerts == []
    assert bus.published == []
    # The rule was evaluated (counter ticked once) — just not fired.
    dims = [d for _, d, _ in meter.records]
    assert dims.count("rule_evaluations") == 1
    assert "alerts_fired" not in dims


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_respects_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    asset = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant, min_confidence=0.0, cooldown_s=60)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    def _ev() -> Event:
        return _attribution_event(
            tenant_id=tenant,
            asset_id=asset,
            zone_id=uuid4(),
            site_id=uuid4(),
            confidence=1.0,
        )

    await evaluator.on_attribution_settled(_ev())
    await evaluator.on_attribution_settled(_ev())

    # Second event suppressed by cooldown for the same (tenant, rule, asset).
    assert len(fake_rules.alerts) == 1
    triggered = [t for t, _ in bus.published if t == Topic.ALERT_TRIGGERED]
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_attribution_settled_cooldown_keyed_per_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown is scoped to (tenant, rule, asset) — a different asset
    triggers an independent alert."""
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant, cooldown_s=60)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    await evaluator.on_attribution_settled(
        _attribution_event(
            tenant_id=tenant,
            asset_id=uuid4(),
            zone_id=uuid4(),
            site_id=uuid4(),
        )
    )
    await evaluator.on_attribution_settled(
        _attribution_event(
            tenant_id=tenant,
            asset_id=uuid4(),
            zone_id=uuid4(),
            site_id=uuid4(),
        )
    )

    assert len(fake_rules.alerts) == 2


# ---------------------------------------------------------------------------
# Fan-out across event_types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_matches_all_on_inference_event_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single attribution_settled event must match on_inference rules
    of all three event_types (location, geolocation, temperature)
    because the payload itself is event_type-agnostic."""
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    loc_rule = _make_on_inference_rule(tenant_id=tenant, event_type="location")
    geo_rule = _make_on_inference_rule(tenant_id=tenant, event_type="geolocation")
    tmp_rule = _make_on_inference_rule(tenant_id=tenant, event_type="temperature")
    fake_rules = _FakeRulesService([loc_rule, geo_rule, tmp_rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    await evaluator.on_attribution_settled(
        _attribution_event(
            tenant_id=tenant,
            asset_id=uuid4(),
            zone_id=uuid4(),
            site_id=uuid4(),
        )
    )

    assert len(fake_rules.alerts) == 3


# ---------------------------------------------------------------------------
# Malformed / defensive paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_no_op_for_missing_tenant_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    fake_rules = _FakeRulesService([])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    bad_event = Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=datetime.now(UTC),
        payload={"asset_id": str(uuid4()), "zone_id": str(uuid4())},
    )

    await evaluator.on_attribution_settled(bad_event)

    assert fake_rules.alerts == []
    assert bus.published == []


@pytest.mark.asyncio
async def test_attribution_settled_no_op_for_invalid_tenant_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    fake_rules = _FakeRulesService([])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    bad_event = Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": "not-a-uuid",
            "asset_id": str(uuid4()),
            "zone_id": str(uuid4()),
        },
    )

    await evaluator.on_attribution_settled(bad_event)

    assert fake_rules.alerts == []


@pytest.mark.asyncio
async def test_attribution_settled_no_op_for_missing_asset_or_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    # Missing asset_id
    await evaluator.on_attribution_settled(
        Event(
            id=uuid4(),
            topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
            timestamp=datetime.now(UTC),
            payload={"tenant_id": str(tenant), "zone_id": str(uuid4())},
        )
    )
    # Missing zone_id
    await evaluator.on_attribution_settled(
        Event(
            id=uuid4(),
            topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
            timestamp=datetime.now(UTC),
            payload={"tenant_id": str(tenant), "asset_id": str(uuid4())},
        )
    )

    assert fake_rules.alerts == []


@pytest.mark.asyncio
async def test_attribution_settled_no_op_for_unparseable_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant)
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    bad = Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(tenant),
            "asset_id": str(uuid4()),
            "zone_id": str(uuid4()),
            "confidence": "not-a-number",
        },
    )

    await evaluator.on_attribution_settled(bad)

    assert fake_rules.alerts == []


# ---------------------------------------------------------------------------
# Action payload provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_settled_alert_payload_includes_action_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``ALERT_TRIGGERED`` payload must carry the rule's
    ``action_type``/``action_config`` so action workers can dispatch
    without a second DB lookup — matching the convention used by the
    other rule paths."""
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_on_inference_rule(tenant_id=tenant)
    rule.action_type = "webhook"
    rule.action_config = {"url": "https://example.invalid/hook"}
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    await evaluator.on_attribution_settled(
        _attribution_event(
            tenant_id=tenant,
            asset_id=uuid4(),
            zone_id=uuid4(),
            site_id=uuid4(),
        )
    )

    alert_events = [ev for t, ev in bus.published if t == Topic.ALERT_TRIGGERED]
    assert len(alert_events) == 1
    payload = alert_events[0].payload
    assert payload["action_type"] == "webhook"
    assert payload["action_config"] == {"url": "https://example.invalid/hook"}
    assert payload["device_id"] is None
    assert payload["severity"] == "info"
