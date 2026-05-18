"""Conformance test for the Phase C dispatcher-layer envelope upgrade.

Asserts the ADR-021 v2 §"Outbound envelope" contract end-to-end at the
webhook dispatcher boundary (Sprint 41 Phase C / C3):

- (a) Legacy rules (``event_type IS NULL``) retain every historical
  payload field unchanged AND gain the five new top-level fields with
  safe defaults (``confidence=1.0``, ``keySet=[]``,
  ``eventConfigurationId=str(rule_id)``, ``categoryId=None``,
  ``labels=[]``).
- (b) Signaling rules (``event_type IS NOT NULL``) populate
  ``confidence`` from ``confidence_threshold``, ``keySet`` from the
  event_type table, and ``eventConfigurationId`` from the rule id.
- (c) Non-``ALERT_TRIGGERED`` events (raw tag reads, telemetry) emit
  the pre-Phase-C envelope shape unchanged (no new fields injected).

Uses httpx ``MockTransport`` to capture the on-the-wire JSON body so the
test asserts what *consumers actually see*, not just what the in-memory
dict contains. Companion to ``test_signaling_envelope.py`` (pure
builder tests).
"""

from __future__ import annotations

import json
import uuid
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

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMeter:
    """Stand-in for ``UsageMeter`` — captures calls for assertion if needed."""

    def __init__(self) -> None:
        self.records: list[tuple[UUID, str, str]] = []

    def record(self, tenant_id: UUID, dimension: str, unit: str, count: int = 1) -> None:
        self.records.append((tenant_id, dimension, unit))


def _make_integration_response(
    *,
    integration_id: UUID,
    tenant_id: UUID,
    events: list[str],
    enrichments: dict[str, str] | None = None,
) -> IntegrationResponse:
    now = datetime.now(UTC)
    return IntegrationResponse(
        id=integration_id,
        tenant_id=tenant_id,
        name="test-webhook",
        type="webhook",
        events=events,
        config={"url": "https://example.invalid/hook"},
        enabled=True,
        status="active",
        health_status="healthy",
        filters=None,
        enrichments=enrichments,
        last_triggered=None,
        created_at=now,
        updated_at=now,
    )


def _make_rule_row(
    *,
    rule_id: UUID,
    tenant_id: UUID,
    event_type: str | None,
    confidence_threshold: Decimal,
) -> RuleModel:
    """Build a detached ``RuleModel`` row for ``session.get`` to return.

    Real ORM rows are constructed via session — but the envelope builder
    only reads the bare columns, so a plain ``RuleModel(...)`` with the
    needed attributes set is sufficient and avoids touching a DB.
    """
    row = RuleModel(
        id=rule_id,
        tenant_id=tenant_id,
        name="test-rule",
        condition_type=("threshold" if event_type is None else f"signaling.{event_type}.on_change"),
        condition_config={},
        action_type="webhook",
        action_config={},
        enabled=True,
        event_type=event_type,
        confidence_threshold=confidence_threshold,
        category_ids=[],
    )
    return row


def _build_dispatcher_with_fake_session(
    *,
    integrations: list[IntegrationResponse],
    rule_row: RuleModel | None,
    captured_bodies: list[dict[str, Any]],
) -> WebhookDispatcher:
    """Wire a dispatcher whose session_factory returns a fully-mocked session.

    The mocked session:
    - Returns ``integrations`` from ``IntegrationService.get_enabled_for_event``
      (we patch the service class to a stub that returns it directly).
    - Returns ``rule_row`` from ``session.get(RuleModel, ...)``.
    - Returns a stub ``IntegrationModel`` row from ``session.get(IntegrationModel, ...)``
      so the post-deliver ``last_triggered`` update works.
    - No-ops on ``commit``, ``add``, ``flush``.
    """
    session = MagicMock()
    session.commit = AsyncMock(return_value=None)
    session.add = MagicMock(return_value=None)
    session.flush = AsyncMock(return_value=None)

    # ``session.get`` is dispatched by model class — return rule or
    # integration row depending on the type the caller asks for.
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
    async def _factory() -> Any:
        yield session

    dispatcher = WebhookDispatcher(
        session_factory=_factory,  # type: ignore[arg-type]
        usage_meter=_FakeMeter(),  # type: ignore[arg-type]
    )

    # Replace the IntegrationService class in the webhook module so it
    # short-circuits to our integration list without touching the DB.
    import tagpulse.integrations.webhook as webhook_mod

    class _FakeIntegrationService:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_enabled_for_event(
            self, _tenant_id: UUID, _event_type: str
        ) -> list[IntegrationResponse]:
            return integrations

    webhook_mod.IntegrationService = _FakeIntegrationService  # type: ignore[misc]

    # Wire an httpx client backed by ``MockTransport`` so we capture
    # exactly the on-the-wire body that a consumer would receive.
    def _handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True})

    dispatcher._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return dispatcher


# ---------------------------------------------------------------------------
# (a) Legacy-rule contract — historical fields preserved + safe defaults added
# ---------------------------------------------------------------------------


class TestLegacyRuleEnvelope:
    """Legacy rules retain pre-Phase-C shape AND gain the five new fields."""

    async def test_legacy_payload_carries_all_historical_alert_fields(
        self,
    ) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        alert_id = uuid.uuid4()
        device_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                event_type=None,  # legacy
                confidence_threshold=Decimal("0.0"),
            ),
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=alert_id,
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "alert_id": str(alert_id),
                    "tenant_id": str(tenant_id),
                    "rule_id": str(rule_id),
                    "device_id": str(device_id),
                    "severity": "warning",
                    "message": "legacy threshold breached",
                    "action_type": "notification",
                    "action_config": {"channel": "email"},
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        assert len(captured) == 1
        body = captured[0]

        # Historical envelope keys preserved verbatim.
        assert body["messageSource"] == "alert.triggered"
        assert body["tenantId"] == str(tenant_id)
        assert "enqueuedTime" in body
        assert body["enrichments"] == {}
        # Inner ``data`` block is the original event payload, untouched.
        assert body["data"]["alert_id"] == str(alert_id)
        assert body["data"]["rule_id"] == str(rule_id)
        assert body["data"]["device_id"] == str(device_id)
        assert body["data"]["severity"] == "warning"
        assert body["data"]["message"] == "legacy threshold breached"
        assert body["data"]["action_type"] == "notification"
        assert body["data"]["action_config"] == {"channel": "email"}

    async def test_legacy_payload_gains_five_new_fields_with_safe_defaults(
        self,
    ) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                event_type=None,
                confidence_threshold=Decimal("0.0"),
            ),
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "alert_id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "rule_id": str(rule_id),
                    "device_id": None,
                    "severity": "warning",
                    "message": "x",
                    "action_type": "notification",
                    "action_config": {},
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        assert body["confidence"] == 1.0
        assert body["keySet"] == []
        assert body["eventConfigurationId"] == str(rule_id)
        assert body["categoryId"] is None
        assert body["labels"] == []


# ---------------------------------------------------------------------------
# (b) Signaling-rule contract — populated confidence + keySet + rule id
# ---------------------------------------------------------------------------


class TestSignalingRuleEnvelope:
    """Signaling rules populate the envelope from the rule columns."""

    @pytest.mark.parametrize(
        ("event_type", "expected_key_set"),
        [
            ("location", ["asset_id", "zone_id"]),
            ("geolocation", ["asset_id", "site_id"]),
            ("geofencing", ["asset_id", "zone_id"]),
            ("temperature", ["asset_id"]),
        ],
    )
    async def test_signaling_payload_populates_envelope_per_event_type(
        self, event_type: str, expected_key_set: list[str]
    ) -> None:
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=_make_rule_row(
                rule_id=rule_id,
                tenant_id=tenant_id,
                event_type=event_type,
                confidence_threshold=Decimal("0.75"),
            ),
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "alert_id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "rule_id": str(rule_id),
                    "device_id": None,
                    "severity": "info",
                    "message": "signaling event fired",
                    "action_type": "notification",
                    "action_config": {},
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        # Phase-C contract: confidence + keySet populated, eventConfigurationId non-null.
        assert body["confidence"] == pytest.approx(0.75)
        assert body["keySet"] == expected_key_set
        assert body["eventConfigurationId"] == str(rule_id)
        # categoryId + labels stay at safe defaults — Phase D will populate
        # these from the matched-entity lookup.
        assert body["categoryId"] is None
        assert body["labels"] == []
        # Historical fields still present, unchanged.
        assert body["messageSource"] == "alert.triggered"
        assert body["data"]["rule_id"] == str(rule_id)


# ---------------------------------------------------------------------------
# (c) Non-alert events — pre-Phase-C envelope shape preserved
# ---------------------------------------------------------------------------


class TestNonAlertEventEnvelope:
    """Raw broadcast events (tag reads, telemetry) get no new fields."""

    async def test_tag_read_event_does_not_add_envelope_fields(self) -> None:
        tenant_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["tag_read.created"],
                )
            ],
            rule_row=None,  # not an alert — rule lookup must not happen
            captured_bodies=captured,
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
                    "signal_strength": -42,
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        # Pre-Phase-C envelope shape — no new top-level keys.
        assert body["messageSource"] == "tag_read.created"
        assert "confidence" not in body
        assert "keySet" not in body
        assert "eventConfigurationId" not in body
        assert "categoryId" not in body
        assert "labels" not in body
        # Inner data preserved.
        assert body["data"]["tag_id"] == "EPC-001"


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


class TestEnvelopeDefensivePaths:
    """Edge cases — missing rule_id, missing rule row, malformed UUID."""

    async def test_alert_with_missing_rule_id_emits_no_envelope_fields(
        self,
    ) -> None:
        # Defensive: if a producer publishes ALERT_TRIGGERED without a
        # rule_id (malformed), the dispatcher falls back to the pre-
        # Phase-C envelope shape rather than crashing.
        tenant_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=None,
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "tenant_id": str(tenant_id),
                    # rule_id deliberately omitted
                    "severity": "warning",
                    "message": "malformed alert",
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        assert "confidence" not in body
        assert "keySet" not in body
        assert "eventConfigurationId" not in body

    async def test_alert_with_unknown_rule_id_emits_safe_defaults(
        self,
    ) -> None:
        # If the rule was deleted between publish and dispatch, ``session.get``
        # returns None — the envelope still emits safe defaults using the
        # rule_id from the payload.
        tenant_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=None,  # rule deleted / not found
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "alert_id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "rule_id": str(rule_id),
                    "severity": "warning",
                    "message": "rule-since-deleted",
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        # Treated as legacy: safe defaults with the original rule_id.
        assert body["confidence"] == 1.0
        assert body["keySet"] == []
        assert body["eventConfigurationId"] == str(rule_id)
        assert body["categoryId"] is None
        assert body["labels"] == []

    async def test_alert_with_malformed_rule_id_emits_no_envelope_fields(
        self,
    ) -> None:
        tenant_id = uuid.uuid4()
        integration_id = uuid.uuid4()

        captured: list[dict[str, Any]] = []
        dispatcher = _build_dispatcher_with_fake_session(
            integrations=[
                _make_integration_response(
                    integration_id=integration_id,
                    tenant_id=tenant_id,
                    events=["alert.triggered"],
                )
            ],
            rule_row=None,
            captured_bodies=captured,
        )

        try:
            event = Event(
                id=uuid.uuid4(),
                topic=Topic.ALERT_TRIGGERED,
                timestamp=datetime.now(UTC),
                payload={
                    "tenant_id": str(tenant_id),
                    "rule_id": "not-a-uuid",
                    "severity": "warning",
                    "message": "malformed",
                },
            )
            await dispatcher.on_event(event)
        finally:
            await dispatcher.stop()

        body = captured[0]
        assert "confidence" not in body
        assert "eventConfigurationId" not in body
