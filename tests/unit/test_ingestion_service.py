"""Unit tests for the ingestion service using fake dependencies."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import TagReadCreate, TagReadResponse


class FakeTagReadRepository:
    """In-memory tag read repository for unit tests."""

    def __init__(self) -> None:
        self.reads: list[TagReadResponse] = []
        self.rejections: list[tuple[UUID, TagReadCreate, str]] = []

    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagReadResponse:
        response = TagReadResponse(
            id=uuid4(),
            device_id=read.device_id,
            tag_id=read.tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            created_at=datetime.now(UTC),
        )
        self.reads.append(response)
        return response

    async def insert_batch(
        self, tenant_id: UUID, reads: list[TagReadCreate]
    ) -> list[TagReadResponse]:
        return [await self.insert(tenant_id, read) for read in reads]

    async def record_rejection(self, tenant_id: UUID, read: TagReadCreate, reason: str) -> None:
        self.rejections.append((tenant_id, read, reason))

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        tag_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        return self.reads[:limit]


@pytest.fixture
def fake_repo() -> FakeTagReadRepository:
    return FakeTagReadRepository()


@pytest.fixture
def event_bus() -> AsyncEventBus:
    return AsyncEventBus(capacity=100)


@pytest.fixture
def service(fake_repo: FakeTagReadRepository, event_bus: AsyncEventBus) -> IngestionService:
    return IngestionService(repo=fake_repo, event_bus=event_bus)


class TestIngestionService:
    async def test_ingest_single_read(
        self, service: IngestionService, fake_repo: FakeTagReadRepository
    ) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="TAG001",
            timestamp=datetime.now(UTC),
            signal_strength=-42.0,
        )
        result = await service.ingest(uuid4(), read)
        assert result.tag_id == "TAG001"
        assert result.signal_strength == -42.0
        assert len(fake_repo.reads) == 1

    async def test_ingest_publishes_event(
        self, service: IngestionService, event_bus: AsyncEventBus
    ) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="TAG002",
            timestamp=datetime.now(UTC),
        )
        await service.ingest(uuid4(), read)
        # Event was published to the bus queue
        queue = event_bus._queues.get(Topic.TAG_READ_CREATED)
        assert queue is not None
        assert queue.qsize() == 1

    async def test_ingest_batch(
        self, service: IngestionService, fake_repo: FakeTagReadRepository
    ) -> None:
        reads = [
            TagReadCreate(
                device_id=uuid4(),
                tag_id=f"TAG{i:03d}",
                timestamp=datetime.now(UTC),
            )
            for i in range(5)
        ]
        count, rejected = await service.ingest_batch(uuid4(), reads)
        assert count == 5
        assert rejected == 0
        assert len(fake_repo.reads) == 5


class TestBackfillFlag:
    """Sprint 58 Q1 — ``?backfill=true`` skips alert evaluation but otherwise
    runs the full ingest pipeline.

    The hard constraint is enforced two ways: the read still lands in the
    repo (so timelines and dashboards reflect history), and the event still
    publishes (so telemetry rollups and other subscribers run) — but the
    payload carries ``backfill: True`` so the rule evaluator and read-frequency
    analytics module short-circuit before doing any work.
    """

    async def _drain_event(self, event_bus: AsyncEventBus, topic: Topic) -> dict:
        queue = event_bus._queues.get(topic)
        assert queue is not None
        assert queue.qsize() == 1
        event = await queue.get()
        return event.payload

    async def test_ingest_default_payload_has_no_backfill_flag(
        self, service: IngestionService, event_bus: AsyncEventBus
    ) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="TAG100",
            timestamp=datetime.now(UTC),
        )
        await service.ingest(uuid4(), read)
        payload = await self._drain_event(event_bus, Topic.TAG_READ_CREATED)
        assert "backfill" not in payload

    async def test_ingest_with_backfill_stamps_payload_and_persists(
        self,
        service: IngestionService,
        fake_repo: FakeTagReadRepository,
        event_bus: AsyncEventBus,
    ) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="TAG101",
            timestamp=datetime.now(UTC),
        )
        result = await service.ingest(uuid4(), read, backfill=True)
        # Full pipeline still ran: read persisted, event published.
        assert result.tag_id == "TAG101"
        assert len(fake_repo.reads) == 1
        payload = await self._drain_event(event_bus, Topic.TAG_READ_CREATED)
        assert payload.get("backfill") is True

    async def test_ingest_batch_with_backfill_stamps_every_payload(
        self,
        service: IngestionService,
        fake_repo: FakeTagReadRepository,
        event_bus: AsyncEventBus,
    ) -> None:
        reads = [
            TagReadCreate(
                device_id=uuid4(),
                tag_id=f"TAGB{i:03d}",
                timestamp=datetime.now(UTC),
            )
            for i in range(3)
        ]
        count, rejected = await service.ingest_batch(uuid4(), reads, backfill=True)
        assert count == 3
        assert rejected == 0
        assert len(fake_repo.reads) == 3
        queue = event_bus._queues.get(Topic.TAG_READ_CREATED)
        assert queue is not None
        assert queue.qsize() == 3
        for _ in range(3):
            event = await queue.get()
            assert event.payload.get("backfill") is True

    async def test_backfill_flag_does_not_leak_to_next_call(
        self, service: IngestionService, event_bus: AsyncEventBus
    ) -> None:
        # ContextVar isolation: a backfill call must not contaminate the next
        # plain ingest on the same service instance.
        await service.ingest(
            uuid4(),
            TagReadCreate(
                device_id=uuid4(),
                tag_id="TAG_BF",
                timestamp=datetime.now(UTC),
            ),
            backfill=True,
        )
        await service.ingest(
            uuid4(),
            TagReadCreate(
                device_id=uuid4(),
                tag_id="TAG_LIVE",
                timestamp=datetime.now(UTC),
            ),
        )
        queue = event_bus._queues.get(Topic.TAG_READ_CREATED)
        assert queue is not None
        assert queue.qsize() == 2
        first = await queue.get()
        second = await queue.get()
        assert first.payload.get("backfill") is True
        assert "backfill" not in second.payload
