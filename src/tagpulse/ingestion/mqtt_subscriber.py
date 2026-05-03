"""MQTT subscriber — connects to broker and ingests tag read and status messages."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import aiomqtt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.api.services.device_service import DeviceService
from tagpulse.api.services.telemetry_model_service import TelemetryModelService
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.events.protocol import EventBus
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import (
    DeviceEventPayload,
    DeviceStatusUpdate,
    LocationPayload,
    TagReadCreate,
    TelemetryReading,
    TelemetrySingle,
)
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository
from tagpulse.repositories.timescaledb.telemetry import TimescaleTelemetryRepository

logger = logging.getLogger(__name__)

# Wildcard catches all per-device topic suffixes — handler branches on suffix.
TOPIC_FILTER = "tenants/+/devices/+/+"
KNOWN_SUFFIXES = {"tag-reads", "status", "telemetry", "location", "events"}


def _parse_topic(topic: str) -> tuple[UUID | None, UUID | None, str | None]:
    """Extract tenant_id, device_id, and type from tenant-scoped topic."""
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
            await client.subscribe(TOPIC_FILTER)
            logger.info("MQTT subscribed to %s", TOPIC_FILTER)
            async for message in client.messages:
                await self._handle_message(message)

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Route message to the appropriate handler with a fresh DB session."""
        tenant_id, device_id, topic_type = _parse_topic(str(message.topic))
        if tenant_id is None or device_id is None or topic_type is None:
            logger.warning("Skipping message with unparseable topic: %s", message.topic)
            return
        if topic_type not in KNOWN_SUFFIXES:
            logger.warning(
                "Unknown topic suffix '%s' for device %s — dropping",
                topic_type,
                device_id,
            )
            return

        if topic_type == "tag-reads":
            await self._handle_tag_read(tenant_id, device_id, message)
        elif topic_type == "status":
            await self._handle_status(tenant_id, device_id, message)
        elif topic_type == "telemetry":
            await self._handle_telemetry(tenant_id, device_id, message)
        elif topic_type == "location":
            await self._handle_location(tenant_id, device_id, message)
        elif topic_type == "events":
            await self._handle_device_event(tenant_id, device_id, message)

    async def _handle_tag_read(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
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
                ingestion_service = self._build_ingestion_service(session)
                await ingestion_service.ingest(tenant_id, read)
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

    async def _handle_telemetry(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping telemetry with invalid JSON on topic %s", message.topic)
            return

        if isinstance(payload.get("readings"), list):
            readings_raw: list[dict[str, Any]] = list(payload["readings"])
        else:
            payload.setdefault("device_id", str(device_id))
            try:
                single = TelemetrySingle(**payload)
            except ValueError:
                logger.warning(
                    "Skipping telemetry with invalid schema on topic %s: %s",
                    message.topic,
                    payload,
                )
                return
            readings_raw = [
                {
                    "timestamp": single.timestamp,
                    "metric_name": single.metric_name,
                    "metric_value": single.metric_value,
                    "unit": single.unit,
                    "metadata": single.metadata,
                }
            ]
        try:
            readings = [TelemetryReading(**r) for r in readings_raw]
        except ValueError:
            logger.warning(
                "Skipping telemetry with invalid reading schema on %s", message.topic
            )
            return

        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                for reading in readings:
                    await svc.ingest_reading(tenant_id, device_id, reading)
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to ingest telemetry from MQTT: device=%s",
                device_id,
            )

    async def _handle_location(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping location with invalid JSON on topic %s", message.topic)
            return
        payload.setdefault("device_id", str(device_id))
        try:
            location = LocationPayload(**payload)
        except ValueError:
            logger.warning(
                "Skipping location with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            return
        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                await svc.ingest_location(tenant_id, location)
                await session.commit()
        except Exception:
            logger.exception("Failed to ingest location from MQTT: device=%s", device_id)

    async def _handle_device_event(
        self, tenant_id: UUID, device_id: UUID, message: aiomqtt.Message
    ) -> None:
        try:
            payload: dict[str, Any] = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Skipping event with invalid JSON on topic %s", message.topic)
            return
        payload.setdefault("device_id", str(device_id))
        try:
            event = DeviceEventPayload(**payload)
        except ValueError:
            logger.warning(
                "Skipping device-event with invalid schema on topic %s: %s",
                message.topic,
                payload,
            )
            return
        try:
            async with self._session_factory() as session:
                svc = self._build_telemetry_service(session)
                await svc.ingest_device_event(tenant_id, event)
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to ingest device event from MQTT: device=%s", device_id
            )

    # -- Helpers --

    def _build_telemetry_service(self, session: AsyncSession) -> TelemetryService:
        return TelemetryService(
            repo=TimescaleTelemetryRepository(session),
            event_bus=self._event_bus,
            model_service=TelemetryModelService(session),
            device_repo=TimescaleDeviceRepository(session),
        )

    def _build_ingestion_service(self, session: AsyncSession) -> IngestionService:
        from tagpulse.repositories.timescaledb.assets import (
            TimescaleAssetTagBindingRepository,
        )
        from tagpulse.repositories.timescaledb.sites_zones import (
            TimescaleZoneRepository,
        )

        return IngestionService(
            repo=TimescaleTagReadRepository(session),
            event_bus=self._event_bus,
            device_repo=TimescaleDeviceRepository(session),
            telemetry_service=self._build_telemetry_service(session),
            binding_repo=TimescaleAssetTagBindingRepository(session),
            zone_repo=TimescaleZoneRepository(session),
        )
