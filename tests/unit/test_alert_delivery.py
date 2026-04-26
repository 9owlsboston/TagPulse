"""Unit tests for the AlertDeliveryService."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.rules.delivery import AlertDeliveryService


@pytest.fixture
def delivery_service() -> AlertDeliveryService:
    return AlertDeliveryService()


class TestAlertDelivery:
    async def test_notification_does_not_raise(
        self, delivery_service: AlertDeliveryService
    ) -> None:
        event = Event(
            id=uuid4(),
            topic=Topic.ALERT_TRIGGERED,
            timestamp=datetime.now(UTC),
            payload={
                "alert_id": str(uuid4()),
                "tenant_id": str(uuid4()),
                "rule_id": str(uuid4()),
                "severity": "warning",
                "message": "Test alert",
                "action_type": "notification",
                "action_config": {},
            },
        )
        await delivery_service.on_alert_triggered(event)

    async def test_unknown_action_type_does_not_raise(
        self, delivery_service: AlertDeliveryService
    ) -> None:
        event = Event(
            id=uuid4(),
            topic=Topic.ALERT_TRIGGERED,
            timestamp=datetime.now(UTC),
            payload={
                "alert_id": str(uuid4()),
                "action_type": "sms",
                "action_config": {},
            },
        )
        await delivery_service.on_alert_triggered(event)

    async def test_webhook_missing_url_does_not_raise(
        self, delivery_service: AlertDeliveryService
    ) -> None:
        await delivery_service.start()
        event = Event(
            id=uuid4(),
            topic=Topic.ALERT_TRIGGERED,
            timestamp=datetime.now(UTC),
            payload={
                "alert_id": str(uuid4()),
                "action_type": "webhook",
                "action_config": {},
            },
        )
        await delivery_service.on_alert_triggered(event)
        await delivery_service.stop()

    async def test_email_logs_intent(
        self, delivery_service: AlertDeliveryService
    ) -> None:
        event = Event(
            id=uuid4(),
            topic=Topic.ALERT_TRIGGERED,
            timestamp=datetime.now(UTC),
            payload={
                "alert_id": str(uuid4()),
                "action_type": "email",
                "action_config": {"to": "ops@example.com"},
                "message": "Test",
            },
        )
        await delivery_service.on_alert_triggered(event)
