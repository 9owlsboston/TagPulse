"""Webhook dispatcher — delivers events to configured webhook integrations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, Topic
from tagpulse.integrations.service import IntegrationService
from tagpulse.integrations.signaling_envelope import build_envelope
from tagpulse.models.database import IntegrationDeliveryModel, RuleModel

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 5
RETRY_DELAYS = [0, 30, 120, 600, 3600]  # seconds


class WebhookDispatcher:
    """Dispatches events to webhook integrations with retry and delivery logging."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        usage_meter: UsageMeter,
    ) -> None:
        self._session_factory = session_factory
        self._meter = usage_meter
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("WebhookDispatcher started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("WebhookDispatcher stopped")

    async def on_event(self, event: Event) -> None:
        """Handle any EventBus event — find matching webhook integrations and deliver."""
        tenant_id_str = event.payload.get("tenant_id")
        if not tenant_id_str:
            return

        tenant_id = uuid.UUID(tenant_id_str)
        event_type = event.topic.value

        async with self._session_factory() as session:
            service = IntegrationService(session)
            integrations = await service.get_enabled_for_event(tenant_id, event_type)

            # Phase C / ADR-021 v2: for rule-fired events, fetch the
            # source rule once per tick and build the five-field
            # signaling envelope. Resolved here (not per-integration)
            # so multiple subscribed integrations share one DB lookup.
            # Non-ALERT_TRIGGERED topics (raw tag reads, telemetry, etc.)
            # retain the pre-Phase-C payload shape unchanged.
            envelope_fields: dict[str, Any] = {}
            if event.topic == Topic.ALERT_TRIGGERED:
                envelope_fields = await self._build_alert_envelope(session, event.payload)

            for integration in integrations:
                if integration.type != "webhook":
                    continue

                # Apply filters — skip if event doesn't match
                if not _passes_filters(integration.filters, event.payload):
                    continue

                # Build enriched payload envelope. ``envelope_fields`` is
                # spread above ``enrichments`` so a (rare) consumer
                # enrichment with a conflicting key takes precedence —
                # matches the existing "enrichments wins" precedent.
                enriched_payload = {
                    "messageSource": event_type,
                    "tenantId": str(tenant_id),
                    "enqueuedTime": datetime.now(UTC).isoformat(),
                    **envelope_fields,
                    "enrichments": integration.enrichments or {},
                    "data": event.payload,
                }

                await self._deliver(
                    session,
                    tenant_id,
                    integration.id,
                    integration.config,
                    event_type,
                    enriched_payload,
                )

                # Update endpoint health
                from tagpulse.models.database import IntegrationModel

                row = await session.get(IntegrationModel, integration.id)
                if row is not None:
                    row.last_triggered = datetime.now(UTC)

            await session.commit()

    async def _build_alert_envelope(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve the source rule and build the five-field signaling envelope.

        Per ADR-021 v2 Phase C: returns the additive ``confidence`` /
        ``keySet`` / ``eventConfigurationId`` / ``categoryId`` / ``labels``
        fields. Legacy rules (``event_type IS NULL``) get safe defaults
        (1.0 / [] / rule_id / null / []). Returns an empty dict if the
        payload has no ``rule_id`` or it doesn't parse — the dispatcher
        then falls back to the pre-Phase-C envelope shape.

        ``category_id`` and ``labels`` are not resolved from the matched
        entity in Phase C — that work lands in Phase D once the
        OverlappingZones processor populates the matched-entity ID on
        the published payload. For Phase C they stay at the safe-default
        ``None`` / ``[]`` for both legacy and signaling paths.
        """
        rule_id_str = payload.get("rule_id")
        if not rule_id_str:
            return {}
        try:
            rule_uuid = uuid.UUID(rule_id_str)
        except (ValueError, TypeError):
            return {}
        rule_row = await session.get(RuleModel, rule_uuid)
        event_type = rule_row.event_type if rule_row is not None else None
        confidence_threshold = rule_row.confidence_threshold if rule_row is not None else None
        return dict(
            build_envelope(
                rule_id=rule_uuid,
                event_type=event_type,
                confidence_threshold=confidence_threshold,
            )
        )

    async def _deliver(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        integration_id: uuid.UUID,
        config: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """POST payload to webhook URL with signing and delivery logging."""
        url = config.get("url")
        if not url:
            logger.warning("Webhook URL missing for integration %s", integration_id)
            return

        if self._client is None:
            return

        headers: dict[str, str] = config.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}

        body = json.dumps(payload)

        # HMAC signing
        secret = config.get("secret")
        if secret:
            signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-TagPulse-Signature"] = signature

        headers["Content-Type"] = "application/json"

        delivery = IntegrationDeliveryModel(
            id=uuid.uuid4(),
            integration_id=integration_id,
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            last_attempt_at=datetime.now(UTC),
        )

        await self._attempt_delivery(delivery, url, body, headers)
        session.add(delivery)
        self._meter.record(tenant_id, "webhook_deliveries", "requests")

    async def _attempt_delivery(
        self,
        delivery: IntegrationDeliveryModel,
        url: str,
        body: str,
        headers: dict[str, str],
    ) -> None:
        """Attempt delivery with retry on 5xx/network errors."""
        if self._client is None:
            return

        for attempt in range(MAX_RETRY_ATTEMPTS):
            delivery.attempts = attempt + 1
            delivery.last_attempt_at = datetime.now(UTC)

            if attempt > 0:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

            try:
                response = await self._client.post(url, content=body, headers=headers)
                delivery.response_code = response.status_code
                if 200 <= response.status_code < 300:
                    delivery.status = "delivered"
                    logger.info(
                        "Webhook delivered: integration=%s status=%d attempt=%d",
                        delivery.integration_id,
                        response.status_code,
                        delivery.attempts,
                    )
                    return
                if response.status_code < 500:
                    # 4xx = client error, don't retry
                    delivery.status = "failed"
                    delivery.error_message = f"HTTP {response.status_code}"
                    logger.warning(
                        "Webhook client error: integration=%s status=%d",
                        delivery.integration_id,
                        response.status_code,
                    )
                    return
                # 5xx = server error, retry
                delivery.error_message = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                delivery.error_message = str(exc)
                logger.warning(
                    "Webhook attempt %d failed: integration=%s error=%s",
                    delivery.attempts,
                    delivery.integration_id,
                    exc,
                )

        # All retries exhausted
        delivery.status = "dead_letter"
        logger.error(
            "Webhook dead-lettered after %d attempts: integration=%s",
            MAX_RETRY_ATTEMPTS,
            delivery.integration_id,
        )


def _passes_filters(
    filters: list[dict[str, Any]] | None,
    payload: dict[str, Any],
) -> bool:
    """Check if event payload passes all filter conditions."""
    if not filters:
        return True
    for f in filters:
        field = f.get("field", "")
        operator = f.get("operator", "")
        value = f.get("value")
        if value is None:
            continue
        actual = payload.get(field)
        if actual is None:
            return False
        try:
            actual_f = float(actual)
            value_f = float(value)
        except (ValueError, TypeError):
            return False
        if operator == "gt" and not (actual_f > value_f):
            return False
        if operator == "lt" and not (actual_f < value_f):
            return False
        if operator == "gte" and not (actual_f >= value_f):
            return False
        if operator == "lte" and not (actual_f <= value_f):
            return False
        if operator == "eq" and actual_f != value_f:
            return False
    return True
