"""Ingestion service — validates and persists tag read events."""

import logging
import uuid
from datetime import UTC, datetime

from tagpulse.core.otel_metrics import ingestion_counter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import TagReadCreate, TagReadResponse
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository

logger = logging.getLogger(__name__)


class IngestionService:
    """Accepts tag reads, persists them, and publishes internal events."""

    def __init__(
        self,
        repo: TagReadRepository,
        event_bus: EventBus,
        device_repo: DeviceRepository | None = None,
    ) -> None:
        self._repo = repo
        self._event_bus = event_bus
        self._device_repo = device_repo

    async def ingest(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadResponse:
        """Validate, persist, and publish a single tag read."""
        result = await self._repo.insert(tenant_id, read)
        ingestion_counter.add(1, {"tenant_id": str(tenant_id), "protocol": "http"})
        logger.info(
            "Tag read ingested: device=%s tag=%s ts=%s",
            read.device_id,
            read.tag_id,
            read.timestamp,
        )
        if self._device_repo:
            now = datetime.now(UTC)
            await self._device_repo.record_last_seen(tenant_id, read.device_id, now)
            await self._device_repo.record_connection_state(
                tenant_id, read.device_id, "online",
            )
        await self._event_bus.publish(
            Topic.TAG_READ_CREATED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.TAG_READ_CREATED,
                timestamp=datetime.now(UTC),
                payload={
                    "tag_read_id": str(result.id),
                    "tenant_id": str(tenant_id),
                    "device_id": str(read.device_id),
                    "tag_id": read.tag_id,
                    "signal_strength": read.signal_strength,
                },
            ),
        )
        return result

    async def ingest_batch(self, tenant_id: uuid.UUID, reads: list[TagReadCreate]) -> int:
        """Validate, persist, and publish a batch of tag reads."""
        count = await self._repo.insert_batch(tenant_id, reads)
        logger.info("Batch ingested: %d tag reads", count)
        for read in reads:
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
