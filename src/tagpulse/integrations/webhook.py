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
from tagpulse.events.protocol import Event
from tagpulse.integrations.service import IntegrationService
from tagpulse.models.database import IntegrationDeliveryModel

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
            integrations = await service.get_enabled_for_event(
                tenant_id, event_type
            )

            for integration in integrations:
                if integration.type != "webhook":
                    continue

                # Apply filters — skip if event doesn't match
                if not _passes_filters(
                    integration.filters, event.payload
                ):
                    continue

                # Build enriched payload envelope
                enriched_payload = {
                    "messageSource": event_type,
                    "tenantId": str(tenant_id),
                    "enqueuedTime": datetime.now(UTC).isoformat(),
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
            signature = hmac.new(
                secret.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
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
                response = await self._client.post(
                    url, content=body, headers=headers
                )
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
