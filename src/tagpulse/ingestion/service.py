"""Ingestion service — validates and persists tag read events."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.otel_metrics import ingestion_counter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.ingestion.tag_data import cap_tag_data
from tagpulse.models.schemas import (
    Identity,
    TagReadCreate,
    TagReadResponse,
    TelemetryReading,
)
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository
from tagpulse.rfid.epc import decode_epc_hex

logger = logging.getLogger(__name__)


class IngestionService:
    """Accepts tag reads, persists them, and publishes internal events."""

    def __init__(
        self,
        repo: TagReadRepository,
        event_bus: EventBus,
        device_repo: DeviceRepository | None = None,
        telemetry_service: TelemetryService | None = None,
    ) -> None:
        self._repo = repo
        self._event_bus = event_bus
        self._device_repo = device_repo
        self._telemetry_service = telemetry_service

    async def ingest(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadResponse:
        """Validate, persist, and publish a single tag read."""
        normalized = self._normalize(tenant_id, read)
        result = await self._repo.insert(tenant_id, normalized)
        ingestion_counter.add(1, {"tenant_id": str(tenant_id), "protocol": "http"})
        logger.info(
            "Tag read ingested: device=%s tag=%s ts=%s",
            normalized.device_id,
            normalized.tag_id,
            normalized.timestamp,
        )
        if self._device_repo:
            now = datetime.now(UTC)
            await self._device_repo.record_last_seen(
                tenant_id, normalized.device_id, now
            )
            await self._device_repo.record_connection_state(
                tenant_id, normalized.device_id, "online",
            )
        await self._mirror_tag_borne_sensors(tenant_id, normalized, result.id)
        await self._event_bus.publish(
            Topic.TAG_READ_CREATED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.TAG_READ_CREATED,
                timestamp=datetime.now(UTC),
                payload={
                    "tag_read_id": str(result.id),
                    "tenant_id": str(tenant_id),
                    "device_id": str(normalized.device_id),
                    "tag_id": normalized.tag_id,
                    "epc": normalized.identity.epc if normalized.identity else None,
                    "tid": normalized.identity.tid if normalized.identity else None,
                    "signal_strength": normalized.signal_strength,
                },
            ),
        )
        return result

    async def ingest_batch(self, tenant_id: uuid.UUID, reads: list[TagReadCreate]) -> int:
        """Validate, persist, and publish a batch of tag reads."""
        normalized = [self._normalize(tenant_id, r) for r in reads]
        count = await self._repo.insert_batch(tenant_id, normalized)
        logger.info("Batch ingested: %d tag reads", count)
        for read in normalized:
            await self._event_bus.publish(
                Topic.TAG_READ_CREATED,
                Event(
                    id=uuid.uuid4(),
                    topic=Topic.TAG_READ_CREATED,
                    timestamp=datetime.now(UTC),
                    payload={
                        "tenant_id": str(tenant_id),
                        "device_id": str(read.device_id),
                        "tag_id": read.tag_id,
                        "signal_strength": read.signal_strength,
                    },
                ),
            )
        return count

    def _normalize(
        self, tenant_id: uuid.UUID, read: TagReadCreate
    ) -> TagReadCreate:
        """Apply EPC decode, tag_id defaulting, and tag_data inline cap."""
        identity = read.identity
        if identity and identity.epc_hex and not identity.epc:
            scheme, decoded = decode_epc_hex(identity.epc_hex)
            uri = decoded.get("uri") if isinstance(decoded, dict) else None
            identity = Identity(
                epc=uri or identity.epc_hex,
                epc_hex=identity.epc_hex,
                epc_scheme=scheme,
                epc_decoded=decoded or None,
                tid=identity.tid,
                user_memory_hex=identity.user_memory_hex,
            )

        # Determine effective tag_id
        effective_tag_id = read.tag_id
        if not effective_tag_id and identity:
            effective_tag_id = identity.epc or identity.tid or identity.epc_hex
        if not effective_tag_id:
            effective_tag_id = ""

        capped = cap_tag_data(read.tag_data, tenant_id=str(tenant_id))

        return TagReadCreate(
            device_id=read.device_id,
            tag_id=effective_tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            location=read.location,
            identity=identity,
            tag_data=capped,
            reader_antenna=read.reader_antenna,
        )

    async def _mirror_tag_borne_sensors(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        tag_read_id: uuid.UUID,
    ) -> None:
        """Mirror declared numeric tag_data keys into device_telemetry rows.

        Per [docs/design/rfid-tag-data-model.md §6 / D4]: tag-borne sensor
        readings are written to ``device_telemetry`` with provenance metadata
        so analytics treat them uniformly with device-borne metrics.
        """
        if not read.tag_data or self._telemetry_service is None:
            return
        provenance: dict[str, Any] = {
            "source": "tag",
            "tag_read_id": str(tag_read_id),
        }
        if read.identity and read.identity.epc:
            provenance["epc"] = read.identity.epc
        if read.identity and read.identity.tid:
            provenance["tid"] = read.identity.tid

        for key, value in read.tag_data.items():
            if key.startswith("_") or not isinstance(value, int | float):
                continue
            reading = TelemetryReading(
                timestamp=read.timestamp,
                metric_name=key,
                metric_value=float(value),
                metadata=provenance,
            )
            try:
                await self._telemetry_service.ingest_reading(
                    tenant_id, read.device_id, reading
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to mirror tag-borne metric %s for tag_read %s",
                    key,
                    tag_read_id,
                )
