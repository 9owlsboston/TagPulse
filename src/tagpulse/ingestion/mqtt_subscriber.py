"""MQTT subscriber — connects to broker and ingests tag read and status messages."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import aiomqtt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.api.services.device_service import DeviceService
from tagpulse.events.protocol import EventBus
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import DeviceStatusUpdate, TagReadCreate
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository

logger = logging.getLogger(__name__)

TAG_READS_FILTER = "tenants/+/devices/+/tag-reads"
STATUS_FILTER = "tenants/+/devices/+/status"


def _parse_topic(topic: str) -> tuple[UUID | None, UUID | None, str | None]:
    """Extract tenant_id, device_id, and type from tenant-scoped topic.

    Expected format: 'tenants/{tenant_id}/devices/{device_id}/{type}'
    """
    parts = str(topic).split("/")
    if len(parts) == 5 and parts[0] == "tenants" and parts[2] == "devices":
        try:
            tenant_id = UUID(parts[1])
            device_id = UUID(parts[3])
        except ValueError:
            logger.warning("Invalid UUID in MQTT topic: %s", topic)
            return None, None, None
        return tenant_id, device_id, parts[4]
    return None, None, None


class MqttSubscriber:
    """Subscribes to MQTT broker with per-message DB sessions."""

    def __init__(
        self,
        host: str,
        port: int,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._username = username
        self._password = password

    async def run(self) -> None:
        """Connect to broker, subscribe, and process messages until cancelled."""
        logger.info("MQTT subscriber connecting to %s:%d", self._host, self._port)
        async with aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
        ) as client:
            await client.subscribe(TAG_READS_FILTER)
            await client.subscribe(STATUS_FILTER)
            logger.info("MQTT subscribed to %s and %s", TAG_READS_FILTER, STATUS_FILTER)
            async for message in client.messages:
                await self._handle_message(message)

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Route message to the appropriate handler with a fresh DB session."""
        tenant_id, device_id, topic_type = _parse_topic(str(message.topic))
        if tenant_id is None or device_id is None or topic_type is None:
            logger.warning("Skipping message with unparseable topic: %s", message.topic)
            return

        if topic_type == "tag-reads":
            await self._handle_tag_read(tenant_id, device_id, message)
        elif topic_type == "status":
            await self._handle_status(tenant_id, device_id, message)
        else:
            logger.warning("Unknown topic type '%s' for device %s", topic_type, device_id)

    async def _handle_tag_read(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        """Parse and ingest a tag read message with a scoped session."""
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping tag-read with invalid JSON on topic %s", message.topic)
            return

        try:
            read = TagReadCreate(device_id=device_id, **payload)
        except ValueError:
            logger.warning(
                "Skipping tag-read with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            return

        try:
            async with self._session_factory() as session:
                repo = TimescaleTagReadRepository(session)
                service = IngestionService(repo=repo, event_bus=self._event_bus)
                await service.ingest(tenant_id, read)
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to ingest tag read from MQTT: device=%s tag=%s",
                device_id,
                payload.get("tag_id"),
            )

    async def _handle_status(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        """Parse and apply a device status update with a scoped session."""
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping status with invalid JSON on topic %s", message.topic)
            return

        try:
            status = DeviceStatusUpdate(**payload)
        except ValueError:
            logger.warning(
                "Skipping status with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            return

        try:
            async with self._session_factory() as session:
                repo = TimescaleDeviceRepository(session)
                device_svc = DeviceService(repo=repo)
                await device_svc.update_status(
                    tenant_id,
                    device_id,
                    connection_state=status.connection_state,
                    firmware_version=status.firmware_version,
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to update device status from MQTT: device=%s",
                device_id,
            )
