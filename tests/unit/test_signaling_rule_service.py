"""Unit tests for signaling rule service (Sprint 41 Phase B1/B2/B5).

Exercises the signaling-rule extensions to :class:`RulesService` without
spinning up a real database:

* ``kind`` filter on ``list_rules`` \u2014 SQL is verified by patching
  ``session.execute`` to capture the constructed statement.
* Cap enforcement (5 / 6 / admin-override) \u2014 patches
  ``count_active_signaling_rules_for_scope`` to return canned counts and
  asserts the right exception (or no-op) is raised.
* ``validate_signaling_condition_config`` is invoked inside
  ``create_rule`` so a malformed periodic config rejects at the service
  layer, not just at the API boundary.

Integration tests against a live TimescaleDB are deferred to Phase E
when the full evaluator path lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from tagpulse.models.rule_schemas import (
    SIGNALING_DEFAULT_CAP_PER_SCOPE,
    RuleCreate,
    RuleUpdate,
)
from tagpulse.rules import RulesService, SignalingScopeCapExceededError


def _stamping_session() -> MagicMock:
    """Mock session whose ``add()`` backfills ``created_at``/``updated_at``
    on the inserted row. The real DB sets these via ``server_default=now()``,
    but a pure mock never invokes that path — the response builder then
    fails Pydantic ``datetime`` validation."""

    session = MagicMock()
    now = datetime.now(UTC)

    def _add(row: Any) -> None:
        if getattr(row, "created_at", None) is None:
            row.created_at = now
        if getattr(row, "updated_at", None) is None:
            row.updated_at = now

    session.add = _add
    session.flush = AsyncMock()
    return session


def _signaling_create(
    *,
    cadence_minutes: int = 5,
    category_ids: list[UUID] | None = None,
    enabled: bool = True,
    condition_type: str = "signaling.location.periodic",
) -> RuleCreate:
    return RuleCreate(
        name="r",
        condition_type=condition_type,
        condition_config={"cadence_minutes": cadence_minutes},
        action_type="notification",
        action_config={},
        category_ids=category_ids or [],
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_cap_allows_under_default() -> None:
    """A scope with 4 active rules accepts the 5th \u2014 strictly less
    than the cap is allowed."""

    service = RulesService(session=MagicMock())
    service.count_active_signaling_rules_for_scope = AsyncMock(return_value=4)

    await service._enforce_signaling_cap(
        tenant_id=uuid4(),
        event_type="location",
        category_ids=[uuid4()],
        enabled=True,
    )


@pytest.mark.asyncio
async def test_enforce_cap_rejects_at_default() -> None:
    """A scope already holding 5 active rules raises on the 6th."""

    service = RulesService(session=MagicMock())
    service.count_active_signaling_rules_for_scope = AsyncMock(return_value=5)
    cat = uuid4()

    with pytest.raises(SignalingScopeCapExceededError) as excinfo:
        await service._enforce_signaling_cap(
            tenant_id=uuid4(),
            event_type="location",
            category_ids=[cat],
            enabled=True,
        )
    assert excinfo.value.current_count == 5
    assert excinfo.value.cap == SIGNALING_DEFAULT_CAP_PER_SCOPE
    assert excinfo.value.event_type == "location"
    assert excinfo.value.category_id == cat


@pytest.mark.asyncio
async def test_enforce_cap_skips_legacy_rules() -> None:
    """Legacy rules (NULL event_type) never count against any cap."""

    service = RulesService(session=MagicMock())
    # If the service erroneously called the counter, the test would
    # fail because AsyncMock returns a coroutine that resolves to a
    # MagicMock (not an int) and the >= comparison would raise.
    service.count_active_signaling_rules_for_scope = AsyncMock(
        side_effect=AssertionError("must not be called for legacy rules")
    )
    await service._enforce_signaling_cap(
        tenant_id=uuid4(),
        event_type=None,
        category_ids=[uuid4()],
        enabled=True,
    )


@pytest.mark.asyncio
async def test_enforce_cap_skips_disabled_rules() -> None:
    """Disabled rules don't consume cap capacity."""

    service = RulesService(session=MagicMock())
    service.count_active_signaling_rules_for_scope = AsyncMock(
        side_effect=AssertionError("must not be called for disabled rules")
    )
    await service._enforce_signaling_cap(
        tenant_id=uuid4(),
        event_type="location",
        category_ids=[uuid4()],
        enabled=False,
    )


@pytest.mark.asyncio
async def test_enforce_cap_broadcast_scope_when_no_categories() -> None:
    """A rule with empty ``category_ids`` occupies the broadcast scope
    (``category_id=None``) \u2014 the counter is invoked with ``None``."""

    service = RulesService(session=MagicMock())
    counter = AsyncMock(return_value=0)
    service.count_active_signaling_rules_for_scope = counter
    await service._enforce_signaling_cap(
        tenant_id=uuid4(),
        event_type="temperature",
        category_ids=[],
        enabled=True,
    )
    counter.assert_awaited_once()
    call = counter.await_args
    assert call is not None
    # Third positional or ``category_id`` kwarg must be None.
    if "category_id" in call.kwargs:
        assert call.kwargs["category_id"] is None
    else:
        assert call.args[2] is None


@pytest.mark.asyncio
async def test_enforce_cap_multi_category_checks_each_scope() -> None:
    """A rule with N categories occupies N scopes; the counter is
    called once per category and any over-cap scope rejects the
    whole rule."""

    service = RulesService(session=MagicMock())
    cat_full = uuid4()
    cat_empty = uuid4()

    async def fake_count(
        tenant_id: UUID,
        event_type: str,
        category_id: UUID | None,
        *,
        exclude_rule_id: UUID | None = None,
    ) -> int:
        if category_id == cat_full:
            return 5
        return 0

    service.count_active_signaling_rules_for_scope = fake_count  # type: ignore[method-assign]

    with pytest.raises(SignalingScopeCapExceededError) as excinfo:
        await service._enforce_signaling_cap(
            tenant_id=uuid4(),
            event_type="location",
            category_ids=[cat_empty, cat_full],
            enabled=True,
        )
    assert excinfo.value.category_id == cat_full


# ---------------------------------------------------------------------------
# create_rule integration with cap + validator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_invokes_cap_check() -> None:
    """``create_rule`` calls ``_enforce_signaling_cap`` for signaling rules."""

    session = _stamping_session()
    service = RulesService(session=session)

    enforce = AsyncMock()
    service._enforce_signaling_cap = enforce  # type: ignore[method-assign]

    tenant = uuid4()
    rule = _signaling_create(category_ids=[uuid4()])
    await service.create_rule(tenant, rule)

    enforce.assert_awaited_once()
    call = enforce.await_args
    assert call is not None
    assert call.kwargs["tenant_id"] == tenant
    assert call.kwargs["event_type"] == "location"
    assert call.kwargs["enabled"] is True


@pytest.mark.asyncio
async def test_create_rule_with_override_skips_cap_check() -> None:
    """``allow_cap_override=True`` bypasses the cap (admin path)."""

    session = _stamping_session()
    service = RulesService(session=session)

    enforce = AsyncMock(side_effect=AssertionError("must not be called when override is set"))
    service._enforce_signaling_cap = enforce  # type: ignore[method-assign]

    await service.create_rule(
        uuid4(), _signaling_create(category_ids=[uuid4()]), allow_cap_override=True
    )


@pytest.mark.asyncio
async def test_create_rule_rejects_invalid_periodic_config() -> None:
    """A periodic rule without ``cadence_minutes`` is rejected at the
    service layer by ``validate_signaling_condition_config`` \u2014 the
    cap check should never run because validation fails first."""

    from pydantic import ValidationError

    session = MagicMock()
    service = RulesService(session=session)
    enforce = AsyncMock()
    service._enforce_signaling_cap = enforce  # type: ignore[method-assign]

    bad = RuleCreate(
        name="bad-periodic",
        condition_type="signaling.location.periodic",
        condition_config={},  # missing cadence_minutes
        action_type="notification",
        action_config={},
    )
    with pytest.raises(ValidationError):
        await service.create_rule(uuid4(), bad)
    enforce.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rule_legacy_path_unchanged() -> None:
    """Legacy rules don't trigger the cap counter or the signaling
    validator \u2014 the create path stays additive. ``_enforce_signaling_cap``
    is still invoked (it's the gatekeeper) but short-circuits on
    ``event_type=None`` without calling the counter."""

    session = _stamping_session()
    service = RulesService(session=session)

    counter = AsyncMock(side_effect=AssertionError("must not be called for legacy rules"))
    service.count_active_signaling_rules_for_scope = counter  # type: ignore[method-assign]

    legacy = RuleCreate(
        name="legacy threshold",
        condition_type="threshold",
        condition_config={"field": "signal_strength", "operator": "gt", "value": -30},
        action_type="webhook",
        action_config={"url": "https://example.com/h"},
    )
    await service.create_rule(uuid4(), legacy)


# ---------------------------------------------------------------------------
# update_rule cap re-check on scope change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_rule_rechecks_cap_when_enabling_signaling_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Toggling ``enabled=True`` on a previously disabled signaling
    rule must re-run the cap check; if the scope is full the patch
    fails with the same exception as create."""

    session = MagicMock()
    session.flush = AsyncMock()

    # Fake the SELECT returning a disabled signaling rule with one cat.
    cat = uuid4()
    rule_id = uuid4()

    class _Row:
        def __init__(self) -> None:
            now = datetime.now(UTC)
            self.id = rule_id
            self.tenant_id = uuid4()
            self.name = "r"
            self.description = None
            self.event_type = "location"
            self.trigger = "periodic"
            self.processor = None
            self.confidence_threshold = None
            self.category_ids = [cat]
            self.asset_label_filters = None
            self.zone_label_filters = None
            self.site_label_filters = None
            self.integration_ids = None
            self.enabled = False
            self.condition_type = "signaling.location.periodic"
            self.condition_config = {"cadence_minutes": 5}
            self.action_type = "notification"
            self.action_config: dict[str, Any] = {}
            self.scope_device_id = None
            self.created_at = now
            self.updated_at = now

    row = _Row()

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return row

    session.execute = AsyncMock(return_value=_Result())
    service = RulesService(session=session)
    enforce = AsyncMock(
        side_effect=SignalingScopeCapExceededError(
            tenant_id=row.tenant_id,
            event_type="location",
            category_id=cat,
            current_count=5,
            cap=5,
        )
    )
    service._enforce_signaling_cap = enforce  # type: ignore[method-assign]

    with pytest.raises(SignalingScopeCapExceededError):
        await service.update_rule(row.tenant_id, rule_id, RuleUpdate(enabled=True))


@pytest.mark.asyncio
async def test_update_rule_override_bypasses_cap() -> None:
    """``allow_cap_override=True`` on update also skips the cap check."""

    session = MagicMock()
    session.flush = AsyncMock()

    class _Row:
        def __init__(self) -> None:
            now = datetime.now(UTC)
            self.id = uuid4()
            self.tenant_id = uuid4()
            self.name = "r"
            self.description = None
            self.event_type = "location"
            self.trigger = "periodic"
            self.processor = None
            self.confidence_threshold = None
            self.category_ids = [uuid4()]
            self.asset_label_filters = None
            self.zone_label_filters = None
            self.site_label_filters = None
            self.integration_ids = None
            self.enabled = False
            self.condition_type = "signaling.location.periodic"
            self.condition_config = {"cadence_minutes": 5}
            self.action_type = "notification"
            self.action_config: dict[str, Any] = {}
            self.scope_device_id = None
            self.created_at = now
            self.updated_at = now

    row = _Row()

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return row

    session.execute = AsyncMock(return_value=_Result())
    service = RulesService(session=session)
    enforce = AsyncMock(side_effect=AssertionError("override must skip cap"))
    service._enforce_signaling_cap = enforce  # type: ignore[method-assign]

    result = await service.update_rule(
        row.tenant_id,
        row.id,
        RuleUpdate(enabled=True),
        allow_cap_override=True,
    )
    assert result is not None


# ---------------------------------------------------------------------------
# list_rules kind filter \u2014 verify the SQL adds the right WHERE clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_signaling_filter_adds_event_type_isnotnull() -> None:
    """``kind='signaling'`` adds ``event_type IS NOT NULL`` to the
    SELECT. We capture the statement passed to ``session.execute``
    and assert the WHERE clause contains the expected predicate."""

    session = MagicMock()

    class _Result:
        def scalars(self) -> Any:
            return []

    captured: dict[str, Any] = {}

    async def fake_execute(stmt: Any) -> Any:
        captured["stmt"] = stmt
        return _Result()

    session.execute = fake_execute
    service = RulesService(session=session)

    await service.list_rules(uuid4(), kind="signaling")

    rendered = str(captured["stmt"].compile(compile_kwargs={"literal_binds": False}))
    assert "rules.event_type IS NOT NULL" in rendered


@pytest.mark.asyncio
async def test_list_rules_legacy_filter_adds_event_type_isnull() -> None:
    """``kind='legacy'`` filters to ``event_type IS NULL``."""

    session = MagicMock()

    class _Result:
        def scalars(self) -> Any:
            return []

    captured: dict[str, Any] = {}

    async def fake_execute(stmt: Any) -> Any:
        captured["stmt"] = stmt
        return _Result()

    session.execute = fake_execute
    service = RulesService(session=session)

    await service.list_rules(uuid4(), kind="legacy")

    rendered = str(captured["stmt"].compile(compile_kwargs={"literal_binds": False}))
    assert "rules.event_type IS NULL" in rendered


@pytest.mark.asyncio
async def test_list_rules_default_no_kind_filter() -> None:
    """No ``kind`` filter \u2014 the SELECT must not constrain ``event_type``
    so legacy callers see all rules."""

    session = MagicMock()

    class _Result:
        def scalars(self) -> Any:
            return []

    captured: dict[str, Any] = {}

    async def fake_execute(stmt: Any) -> Any:
        captured["stmt"] = stmt
        return _Result()

    session.execute = fake_execute
    service = RulesService(session=session)

    await service.list_rules(uuid4())

    rendered = str(captured["stmt"].compile(compile_kwargs={"literal_binds": False}))
    assert "event_type IS NULL" not in rendered
    assert "event_type IS NOT NULL" not in rendered
