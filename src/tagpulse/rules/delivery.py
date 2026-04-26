"""Alert delivery service — dispatches alerts to configured action targets."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tagpulse.events.protocol import Event

logger = logging.getLogger(__name__)


class AlertDeliveryService:
    """Delivers triggered alerts to their configured action targets (webhook, email, etc.)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("AlertDeliveryService started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("AlertDeliveryService stopped")

    async def on_alert_triggered(self, event: Event) -> None:
        """Handle ALERT_TRIGGERED events — dispatch to action target."""
        payload = event.payload
        alert_id = payload.get("alert_id", "")
        action_type = payload.get("action_type", "notification")
        action_config = payload.get("action_config", {})

        logger.info(
            "Delivering alert %s via %s",
            alert_id,
            action_type,
        )

        if action_type == "webhook":
            await self._deliver_webhook(alert_id, action_config, payload)
        elif action_type == "email":
            await self._deliver_email(alert_id, action_config, payload)
        elif action_type == "notification":
            self._deliver_notification(alert_id, payload)
        else:
            logger.warning("Unknown action type '%s' for alert %s", action_type, alert_id)

    async def _deliver_webhook(
        self,
        alert_id: str,
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """POST alert payload to configured webhook URL."""
        url = config.get("url")
        if not url:
            logger.warning("Webhook URL missing for alert %s", alert_id)
            return

        headers = config.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}

        body = {
            "alert_id": alert_id,
            "tenant_id": payload.get("tenant_id"),
            "rule_id": payload.get("rule_id"),
            "device_id": payload.get("device_id"),
            "severity": payload.get("severity"),
            "message": payload.get("message"),
        }

        if self._client is None:
            logger.error("HTTP client not initialized for alert %s", alert_id)
            return

        try:
            response = await self._client.post(url, json=body, headers=headers)
            logger.info(
                "Webhook delivered: alert=%s url=%s status=%d",
                alert_id,
                url,
                response.status_code,
            )
        except httpx.HTTPError:
            logger.exception(
                "Webhook delivery failed: alert=%s url=%s",
                alert_id,
                url,
            )

    async def _deliver_email(
        self,
        alert_id: str,
        config: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Send alert via email (placeholder — logs intent)."""
        to_addr = config.get("to", "")
        logger.info(
            "Email alert queued: alert=%s to=%s message=%s",
            alert_id,
            to_addr,
            payload.get("message", ""),
        )

    @staticmethod
    def _deliver_notification(alert_id: str, payload: dict[str, Any]) -> None:
        """Log alert to internal notification queue."""
        logger.info(
            "Internal notification: alert=%s severity=%s message=%s",
            alert_id,
            payload.get("severity"),
            payload.get("message"),
        )
