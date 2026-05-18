"""Unit tests for PeriodicSignalingDispatcher (Sprint 41 Phase B3/B5).

Exercises the dispatcher's cadence accounting and rule-discovery loop.
The Phase B shell does not invoke real processor logic, so these tests
patch ``_evaluate_periodic_rule`` and assert that it's called with the
right rules at the right ticks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.models.rule_schemas import RuleResponse
from tagpulse.signaling.periodic_dispatcher import (
    SIGNALING_PERIODIC_CONDITION_TYPES,
    PeriodicSignalingDispatcher,
)

# ---------------------------------------------------------------------------
# Fakes (mirroring tests/unit/test_phase_e_inventory_rules.py conventions)
# ---------------------------------------------------------------------------


class _FakeMeter:
    def __init__(self) -> None:
        self.records: list[tuple[UUID, str, str]] = []

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        self.records.append((tenant_id, dimension, unit))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


class _FakeSession:
    async def commit(self) -> None:
        return None


def _fake_session_factory() -> Any:
    @asynccontextmanager
    async def _factory() -> Any:
        yield _FakeSession()

    return _factory


def _make_periodic_rule(
    *,
    tenant_id: UUID | None = None,
    condition_type: str = "signaling.location.periodic",
    cadence_minutes: int = 5,
    rule_id: UUID | None = None,
) -> RuleResponse:
    now = datetime.now(UTC)
    event_type, trigger = condition_type.split(".")[1], condition_type.split(".")[2]
    return RuleResponse(
        id=rule_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        name=f"periodic-{event_type}",
        description=None,
        condition_type=condition_type,
        condition_config={"cadence_minutes": cadence_minutes},
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        trigger=trigger,
    )


# ---------------------------------------------------------------------------
# Constants guard rail
# ---------------------------------------------------------------------------


def test_periodic_condition_types_match_adr() -> None:
    """The dispatcher must own exactly the three ``*.periodic`` strings
    in the ADR-021 v2 matrix. If a new periodic trigger is added to
    ``SIGNALING_VALID_PAIRS``, this test fails so the dispatcher's
    cross-tenant scan can be extended in lockstep."""
    from tagpulse.models.rule_schemas import SIGNALING_VALID_PAIRS

    expected = sorted(
        f"signaling.{event_type}.periodic"
        for event_type, triggers in SIGNALING_VALID_PAIRS.items()
        if "periodic" in triggers
    )
    assert sorted(SIGNALING_PERIODIC_CONDITION_TYPES) == expected


# ---------------------------------------------------------------------------
# Cadence accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_empty_rules_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No periodic rules in the DB → the evaluator hook is never invoked."""

    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return []

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    hook.assert_not_awaited()
    assert dispatcher._last_fired == {}


@pytest.mark.asyncio
async def test_first_tick_fires_all_due_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rule never previously evaluated is always due on the first tick."""

    rule_a = _make_periodic_rule(condition_type="signaling.location.periodic")
    rule_b = _make_periodic_rule(condition_type="signaling.temperature.periodic")
    rule_c = _make_periodic_rule(condition_type="signaling.geolocation.periodic")

    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            assert sorted(condition_types) == sorted(SIGNALING_PERIODIC_CONDITION_TYPES)
            return [rule_a, rule_b, rule_c]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    assert hook.await_count == 3
    # All three rules have a last_fired stamp now.
    assert set(dispatcher._last_fired.keys()) == {rule_a.id, rule_b.id, rule_c.id}


@pytest.mark.asyncio
async def test_second_tick_within_cadence_skips_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rule with cadence_minutes=10 fired at T=0 must not fire again
    on a tick at T=2min."""

    rule = _make_periodic_rule(cadence_minutes=10)
    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    # Seed last_fired as if rule was evaluated 2 minutes ago.
    dispatcher._last_fired[rule.id] = datetime.now(UTC) - timedelta(minutes=2)

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return [rule]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    hook.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_after_cadence_elapsed_fires_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule with cadence_minutes=5 last fired 6 minutes ago → fires
    again on this tick."""

    rule = _make_periodic_rule(cadence_minutes=5)
    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    seeded_at = datetime.now(UTC) - timedelta(minutes=6)
    dispatcher._last_fired[rule.id] = seeded_at

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return [rule]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    hook.assert_awaited_once()
    # last_fired was advanced past the seeded stamp.
    assert dispatcher._last_fired[rule.id] > seeded_at


@pytest.mark.asyncio
async def test_multiple_cadences_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two rules with different cadences must each fire on their own
    schedule; one being skipped on a tick must not block the other."""

    fast = _make_periodic_rule(cadence_minutes=1)
    slow = _make_periodic_rule(cadence_minutes=60)
    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )
    # fast: fired 2 minutes ago → due. slow: fired 2 minutes ago → not due.
    two_min_ago = datetime.now(UTC) - timedelta(minutes=2)
    dispatcher._last_fired[fast.id] = two_min_ago
    dispatcher._last_fired[slow.id] = two_min_ago

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return [fast, slow]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    assert hook.await_count == 1
    args = hook.await_args
    assert args is not None
    fired_rule = args.args[2]
    assert fired_rule.id == fast.id


@pytest.mark.asyncio
async def test_invalid_cadence_minutes_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A rule whose ``cadence_minutes`` is missing or invalid (the
    Pydantic schema rejects this on writes, but legacy rows may
    predate the migration) must be skipped with a warning rather than
    crash the dispatcher loop."""

    bad = _make_periodic_rule(cadence_minutes=5)
    bad.condition_config = {}  # strip cadence_minutes
    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    hook = AsyncMock()
    monkeypatch.setattr(dispatcher, "_evaluate_periodic_rule", hook)

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return [bad]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    with caplog.at_level("WARNING"):
        await dispatcher.run_once()

    hook.assert_not_awaited()
    assert bad.id not in dispatcher._last_fired
    assert any("invalid cadence_minutes" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_evaluator_hook_records_meter_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase B shell ``_evaluate_periodic_rule`` records a
    ``rule_evaluations`` meter tick \u2014 a cheap proxy for "the
    cadence-driven path executed for this rule" that the metering
    pipeline already exposes per tenant."""

    rule = _make_periodic_rule()
    meter = _FakeMeter()
    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=meter,  # type: ignore[arg-type]
    )

    class _FakeRulesService:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_rules_by_condition_types_all_tenants(
            self, condition_types: list[str]
        ) -> list[RuleResponse]:
            return [rule]

    monkeypatch.setattr("tagpulse.signaling.periodic_dispatcher.RulesService", _FakeRulesService)

    await dispatcher.run_once()

    dims = [d for _, d, _ in meter.records]
    assert "rule_evaluations" in dims


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    """``start()`` / ``stop()`` must be safe to invoke and cancel the
    background task without raising."""

    dispatcher = PeriodicSignalingDispatcher(
        session_factory=_fake_session_factory(),  # type: ignore[arg-type]
        event_bus=_FakeBus(),  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
        tick_interval_s=3600.0,  # long enough we never tick during the test
    )
    await dispatcher.start()
    assert dispatcher._task is not None and not dispatcher._task.done()
    await dispatcher.stop()
    assert dispatcher._task.cancelled() or dispatcher._task.done()
