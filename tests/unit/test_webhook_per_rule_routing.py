"""Sprint 41 Phase E2 — per-rule integration routing in the WebhookDispatcher.

Covers the ADR-021 v2 open-question-#3 decision: a non-empty
``rule.integration_ids`` *replaces* the global broadcast for that rule's
alerts; an empty / null list preserves the legacy broadcast behaviour.

The test fixtures here intentionally mirror the scaffolding in
``test_webhook_envelope.py`` (Phase C tests) so the two test files form a
matched pair: Phase C asserts payload shape, Phase E2 asserts dispatch
fan-out.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.integrations.webhook import WebhookDispatcher
from tagpulse.models.database import IntegrationModel, RuleModel
from tagpulse.models.integration_schemas import IntegrationResponse


class _FakeMeter:
    def __init__(self) -> None:
        self.records: list[tuple[UUID, str, str]] = []

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        self.records.append((tenant_id, dimension, unit))


def _make_integration_response(
    *,
    integration_id: UUID,
    tenant_id: UUID,
    name: str = "test-webhook",
) -> IntegrationResponse:
    now = datetime.now(UTC)
    return IntegrationResponse(
        id=integration_id,
        tenant_id=tenant_id,
        name=name,
        type="webhook",
        events=["alert.triggered"],
        config={"url": f"https://example.invalid/{name}"},
        enabled=True,
        status="active",
        health_status="healthy",
        filters=None,
        enrichments=None,
        last_triggered=None,
        created_at=now,
        updated_at=now,
    )


def _make_rule_row(
    *,
    rule_id: UUID,
    tenant_id: UUID,
    integration_ids: list[UUID] | None,
    event_type: str | None = None,
) -> RuleModel:
    return RuleModel(
        id=rule_id,
        tenant_id=tenant_id,
        name="test-rule",
        condition_type=("threshold" if event_type is None else f"signaling.{event_type}.on_change"),
        condition_config={},
        action_type="webhook",
        action_config={},
        enabled=True,
        event_type=event_type,
        confidence_threshold=Decimal("0.0"),
        category_ids=[],
        integration_ids=integration_ids,
    )


def _build_dispatcher(
    *,
    integrations: list[IntegrationResponse],
    rule_row: RuleModel | None,
    captured_urls: list[str],
) -> WebhookDispatcher:
    session = MagicMock()
    session.commit = AsyncMock(return_value=None)
    session.add = MagicMock(return_value=None)
    session.flush = AsyncMock(return_value=None)

    integration_row = MagicMock()
    integration_row.last_triggered = None

    async def _get(model: Any, _id: Any) -> Any:
        if model is RuleModel:
            return rule_row
        if model is IntegrationModel:
            return integration_row
        return None

    session.get = AsyncMock(side_effect=_get)

    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        yield session

    dispatcher = WebhookDispatcher(
        session_factory=_factory,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    import tagpulse.integrations.webhook as webhook_mod

    class _FakeIntegrationService:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_enabled_for_event(
            self, _tenant_id: UUID, _event_type: str
        ) -> list[IntegrationResponse]:
            return integrations

    webhook_mod.IntegrationService = _FakeIntegrationService  # type: ignore[misc]

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        # Also assert the body parses cleanly so encoding issues surface
        # immediately rather than as silent missed-fanout failures.
        json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return dispatcher


def _alert_event(*, tenant_id: UUID, rule_id: UUID | None) -> Event:
    payload: dict[str, Any] = {
        "alert_id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "severity": "warning",
        "message": "x",
        "action_type": "notification",
        "action_config": {},
    }
    if rule_id is not None:
        payload["rule_id"] = str(rule_id)
    return Event(
        id=uuid.uuid4(),
        topic=Topic.ALERT_TRIGGERED,
        timestamp=datetime.now(UTC),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Broadcast preserved when integration_ids is null / empty
# ---------------------------------------------------------------------------


class TestBroadcastFallback:
    """Empty / null ``integration_ids`` keeps the legacy broadcast."""

    async def test_null_integration_ids_broadcasts_to_all_enabled(self) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()
        int_c = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
                _make_integration_response(
                    integration_id=int_c, tenant_id=tenant_id, name="c"
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id, tenant_id=tenant_id, integration_ids=None
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert len(captured) == 3
        assert {url.rsplit("/", 1)[1] for url in captured} == {"a", "b", "c"}

    async def test_empty_integration_ids_broadcasts_to_all_enabled(self) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id, tenant_id=tenant_id, integration_ids=[]
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert len(captured) == 2


# ---------------------------------------------------------------------------
# Non-empty integration_ids = replace
# ---------------------------------------------------------------------------


class TestPerRuleReplace:
    """Non-empty ``integration_ids`` replaces (does NOT augment) broadcast."""

    async def test_subset_integration_ids_delivers_only_to_subset(self) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()
        int_c = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
                _make_integration_response(
                    integration_id=int_c, tenant_id=tenant_id, name="c"
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                integration_ids=[int_a, int_c],
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert {url.rsplit("/", 1)[1] for url in captured} == {"a", "c"}

    async def test_integration_ids_not_subscribed_to_event_are_skipped(self) -> None:
        # Edge case: the rule allow-lists an integration that
        # ``get_enabled_for_event`` did NOT return (because it isn't
        # subscribed to ``alert.triggered`` or is disabled). The filter
        # intersects with the subscription set — it does not bypass the
        # subscription check (replace, not augment).
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()
        not_subscribed = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                integration_ids=[int_a, not_subscribed],
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert {url.rsplit("/", 1)[1] for url in captured} == {"a"}

    async def test_all_filtered_out_delivers_nothing(self) -> None:
        # If the rule's allow-list and the subscription set are
        # disjoint, the dispatcher delivers nothing. This is the
        # intended behaviour ("replace, not augment") — operators that
        # mis-configure the rule should see zero deliveries rather
        # than silently fall back to broadcast.
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_subscribed = uuid.uuid4()
        unrelated = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_subscribed,
                    tenant_id=tenant_id,
                    name="subscribed",
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                integration_ids=[unrelated],
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert captured == []


# ---------------------------------------------------------------------------
# Signaling-rule path — filter applies the same way for event_type-populated rules
# ---------------------------------------------------------------------------


class TestPerRuleReplaceSignaling:
    """Signaling rules (event_type populated) honour the same filter."""

    @pytest.mark.parametrize(
        "event_type",
        ["location", "geolocation", "geofencing", "temperature"],
    )
    async def test_signaling_rule_routes_only_to_allowlisted(
        self, event_type: str
    ) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        chosen = uuid.uuid4()
        other = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=chosen, tenant_id=tenant_id, name="chosen"
                ),
                _make_integration_response(
                    integration_id=other, tenant_id=tenant_id, name="other"
                ),
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                integration_ids=[chosen],
                event_type=event_type,
            ),
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert {url.rsplit("/", 1)[1] for url in captured} == {"chosen"}


# ---------------------------------------------------------------------------
# Defensive paths — filter does not apply when there's no rule context
# ---------------------------------------------------------------------------


class TestFilterDefensivePaths:
    """No rule_id / unknown rule / non-alert events: filter is bypassed."""

    async def test_missing_rule_id_broadcasts(self) -> None:
        tenant_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
            ],
            rule_row=None,
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(_alert_event(tenant_id=tenant_id, rule_id=None))
        finally:
            await dispatcher.stop()

        assert len(captured) == 2

    async def test_unknown_rule_id_broadcasts(self) -> None:
        # Rule deleted between publish and dispatch — fall back to
        # broadcast rather than dropping the alert silently.
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
            ],
            rule_row=None,  # rule lookup returns None
            captured_urls=captured,
        )

        try:
            await dispatcher.on_event(
                _alert_event(tenant_id=tenant_id, rule_id=rule_id)
            )
        finally:
            await dispatcher.stop()

        assert len(captured) == 2

    async def test_non_alert_event_broadcasts(self) -> None:
        # Non-ALERT_TRIGGERED events have no rule context — filter is
        # never consulted; all subscribed integrations receive the event.
        tenant_id = uuid.uuid4()
        int_a = uuid.uuid4()
        int_b = uuid.uuid4()

        captured: list[str] = []
        dispatcher = _build_dispatcher(
            integrations=[
                _make_integration_response(
                    integration_id=int_a, tenant_id=tenant_id, name="a"
                ),
                _make_integration_response(
                    integration_id=int_b, tenant_id=tenant_id, name="b"
                ),
            ],
            rule_row=None,
            captured_urls=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.TAG_READ_CREATED,
                timestamp=datetime.now(UTC),
                payload={
                    "tenant_id": str(tenant_id),
                    "device_id": str(uuid.uuid4()),
                    "tag_id": "EPC-001",
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        assert len(captured) == 2
