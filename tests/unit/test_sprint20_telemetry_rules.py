"""Sprint 20 unit tests — telemetry.threshold rules + cold-chain template.

Covers the new `Topic.TELEMETRY_RECORDED` rule path, the `telemetry.threshold`
condition evaluator, the per-(tenant, rule, subject) cooldown gate, and the
built-in `lot.cold_chain_breach` rule template.

Reuses the same fake-bus / fake-meter / fake-rules-service shapes as
``test_zone_rules_and_dwell.py`` so behaviour stays consistent across rule
families.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import (
    RuleCreate,
    RuleResponse,
    TelemetryThresholdCondition,
)
from tagpulse.rules.evaluator import _eval_telemetry_threshold
from tagpulse.rules.templates import get_template, get_templates

# -- Fakes (mirror test_zone_rules_and_dwell.py) --


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
    condition_config: dict[str, Any],
) -> RuleResponse:
    now = datetime.now(UTC)
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        name="cold-chain",
        description=None,
        condition_type="telemetry.threshold",
        condition_config=condition_config,
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def _telemetry_event(
    tenant_id: UUID,
    *,
    subject_kind: str,
    subject_id: UUID,
    metric_name: str,
    metric_value: float,
) -> Event:
    return Event(
        id=uuid4(),
        topic=Topic.TELEMETRY_RECORDED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(tenant_id),
            "subject_kind": subject_kind,
            "subject_id": str(subject_id),
            "metric_name": metric_name,
            "metric_value": metric_value,
            "unit": "C",
            "device_id": None,
            "source": "external",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


@pytest.fixture(autouse=True)
def _clear_cooldowns() -> None:
    from tagpulse.rules.evaluator import _RULE_COOLDOWN_UNTIL

    _RULE_COOLDOWN_UNTIL.clear()


# -- Pure evaluator (_eval_telemetry_threshold) --


def test_eval_telemetry_threshold_matches_subject_kind_and_metric() -> None:
    config = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "operator": "gt",
        "value": 8.0,
    }
    payload = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "metric_value": 9.5,
    }
    assert _eval_telemetry_threshold(config, payload) is True


def test_eval_telemetry_threshold_rejects_other_subject_kind() -> None:
    config = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "operator": "gt",
        "value": 8.0,
    }
    payload = {
        "subject_kind": "asset",
        "metric_name": "temperature_c",
        "metric_value": 99.0,
    }
    assert _eval_telemetry_threshold(config, payload) is False


def test_eval_telemetry_threshold_subject_id_pin() -> None:
    sid = str(uuid4())
    config = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "operator": "gt",
        "value": 8.0,
        "subject_id": sid,
    }
    matching = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "metric_value": 9.0,
        "subject_id": sid,
    }
    other = {
        "subject_kind": "lot",
        "metric_name": "temperature_c",
        "metric_value": 9.0,
        "subject_id": str(uuid4()),
    }
    assert _eval_telemetry_threshold(config, matching) is True
    assert _eval_telemetry_threshold(config, other) is False


@pytest.mark.parametrize(
    "op,value,actual,expected",
    [
        ("gt", 8.0, 9.0, True),
        ("gt", 8.0, 8.0, False),
        ("lt", 0.0, -1.0, True),
        ("gte", 8.0, 8.0, True),
        ("lte", 8.0, 8.0, True),
        ("eq", 5.0, 5.0, True),
        ("eq", 5.0, 5.1, False),
    ],
)
def test_eval_telemetry_threshold_operators(
    op: str, value: float, actual: float, expected: bool
) -> None:
    config = {
        "subject_kind": "lot",
        "metric_name": "t",
        "operator": op,
        "value": value,
    }
    payload = {
        "subject_kind": "lot",
        "metric_name": "t",
        "metric_value": actual,
    }
    assert _eval_telemetry_threshold(config, payload) is expected


# -- on_telemetry_recorded full path --


@pytest.mark.asyncio
async def test_on_telemetry_recorded_fires_alert_on_breach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    lot_id = uuid4()
    rule = _make_rule(
        tenant,
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
            "cooldown_s": 0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    bus = _FakeBus()
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=lot_id,
            metric_name="temperature_c",
            metric_value=9.5,
        )
    )
    assert len(fake_rules.alerts) == 1
    alert = fake_rules.alerts[0]
    assert "9.5" in alert["message"]
    assert "lot" in alert["message"]
    assert any(t == Topic.ALERT_TRIGGERED for t, _ in bus.published)


@pytest.mark.asyncio
async def test_on_telemetry_recorded_no_alert_when_under_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_rule(
        tenant,
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=uuid4(),
            metric_name="temperature_c",
            metric_value=4.0,
        )
    )
    assert fake_rules.alerts == []


@pytest.mark.asyncio
async def test_on_telemetry_recorded_per_subject_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeat breach for same subject within cooldown is suppressed; a
    different subject still fires."""
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_rule(
        tenant,
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
            "cooldown_s": 600,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    lot_a = uuid4()
    lot_b = uuid4()
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=lot_a,
            metric_name="temperature_c",
            metric_value=9.0,
        )
    )
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=lot_a,
            metric_name="temperature_c",
            metric_value=10.0,
        )
    )
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=lot_b,
            metric_name="temperature_c",
            metric_value=9.0,
        )
    )
    assert len(fake_rules.alerts) == 2  # one per distinct lot


@pytest.mark.asyncio
async def test_on_telemetry_recorded_no_match_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong metric_name → rule body's eval returns False → no alert."""
    from tagpulse.rules.evaluator import RuleEvaluator

    tenant = uuid4()
    rule = _make_rule(
        tenant,
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
        },
    )
    fake_rules = _FakeRulesService([rule])
    monkeypatch.setattr("tagpulse.rules.evaluator.RulesService", lambda session: fake_rules)
    evaluator = RuleEvaluator(
        session_factory=lambda: _DummyCtx(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    await evaluator.on_telemetry_recorded(
        _telemetry_event(
            tenant,
            subject_kind="lot",
            subject_id=uuid4(),
            metric_name="humidity",  # not the rule's metric
            metric_value=99.0,
        )
    )
    assert fake_rules.alerts == []


# -- Schema validation --


def test_telemetry_threshold_condition_validates() -> None:
    cond = TelemetryThresholdCondition(
        subject_kind="lot",
        metric_name="temperature_c",
        operator="gt",
        value=8.0,
    )
    assert cond.cooldown_s == 300


def test_telemetry_threshold_condition_rejects_unknown_subject_kind() -> None:
    with pytest.raises(ValueError):
        TelemetryThresholdCondition(
            subject_kind="widget",  # type: ignore[arg-type]
            metric_name="t",
            operator="gt",
            value=1.0,
        )


def test_rule_create_accepts_telemetry_threshold() -> None:
    rule = RuleCreate(
        name="cc",
        condition_type="telemetry.threshold",
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
        },
        action_type="notification",
        action_config={},
    )
    assert rule.condition_type == "telemetry.threshold"


# -- Built-in templates --


def test_lot_cold_chain_breach_template_present() -> None:
    tpl = get_template("lot.cold_chain_breach")
    assert tpl is not None
    assert tpl.condition_type == "telemetry.threshold"
    assert tpl.condition_config["subject_kind"] == "lot"
    assert tpl.condition_config["metric_name"] == "temperature_c"
    assert tpl.requires_subject_kind == "lot"


def test_templates_list_includes_known_keys() -> None:
    keys = {t.key for t in get_templates()}
    assert {"lot.cold_chain_breach", "asset.high_temperature"}.issubset(keys)


def test_lot_cold_chain_breach_template_round_trips_into_rule_create() -> None:
    """The template's payload must be directly POST-able to /rules."""
    tpl = get_template("lot.cold_chain_breach")
    assert tpl is not None
    rule = RuleCreate(
        name=tpl.name,
        description=tpl.description,
        condition_type=tpl.condition_type,
        condition_config=tpl.condition_config,
        action_type=tpl.action_type,
        action_config=tpl.action_config,
    )
    assert rule.condition_config["value"] == 8.0


# -- Sprint 18/19/20 audit regression: device-scoped TELEMETRY_RECORDED --


@pytest.mark.asyncio
async def test_telemetry_service_publishes_telemetry_recorded_for_device() -> None:
    """Audit fix: ``TelemetryService.ingest_reading`` writes a
    ``subject_kind='device'`` row to ``telemetry_readings``; it must
    publish ``Topic.TELEMETRY_RECORDED`` so a ``telemetry.threshold``
    rule with ``subject_kind='device'`` actually fires.

    Without this, the schema accepts the rule but no event matches —
    silent contract gap.
    """
    from tagpulse.api.services.telemetry_model_service import TelemetryModelService
    from tagpulse.api.services.telemetry_service import TelemetryService
    from tagpulse.models.schemas import TelemetryReading, TelemetryResponse

    tenant = uuid4()
    device = uuid4()

    class _FakeRepo:
        async def insert_reading(
            self,
            tenant_id: UUID,
            device_id: UUID,
            reading: TelemetryReading,
            *,
            metadata: dict[str, Any] | None = None,
        ) -> TelemetryResponse:
            return TelemetryResponse(
                id=uuid4(),
                device_id=device_id,
                timestamp=reading.timestamp,
                metric_name=reading.metric_name,
                metric_value=reading.metric_value,
                unit=reading.unit,
                metadata=reading.metadata,
            )

        async def quarantine(
            self,
            tenant_id: UUID,
            device_id: UUID,
            reading: TelemetryReading,
            reason: str,
        ) -> None:  # pragma: no cover - unused in this test
            return None

    class _FakeModelService:
        async def get_by_device_type(self, tenant_id: UUID, device_type: str) -> Any:
            class _M:
                name = "temperature_c"
                unit = "C"
                min_value = -40.0
                max_value = 100.0

            class _Model:
                metrics = [_M()]

            return _Model()

    bus = _FakeBus()
    svc = TelemetryService(
        repo=_FakeRepo(),  # type: ignore[arg-type]
        event_bus=bus,  # type: ignore[arg-type]
        model_service=_FakeModelService(),  # type: ignore[arg-type]
        device_repo=None,
    )
    _ = TelemetryModelService  # imported only to assert symbol resolves

    response = await svc.ingest_reading(
        tenant,
        device,
        TelemetryReading(
            timestamp=datetime.now(UTC),
            metric_name="temperature_c",
            metric_value=9.5,
            unit="C",
        ),
        device_type="rfid_reader",
    )
    assert response is not None
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.TELEMETRY_RECORDED
    assert event.payload["subject_kind"] == "device"
    assert event.payload["subject_id"] == str(device)
    assert event.payload["metric_value"] == 9.5
    assert event.payload["source"] == "device"
