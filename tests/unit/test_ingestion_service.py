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
        count = await service.ingest_batch(uuid4(), reads)
        assert count == 5
        assert len(fake_repo.reads) == 5
