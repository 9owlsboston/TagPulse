"""Sprint 16 — ingestion clock-window enforcement tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.ingestion.clock import (
    REASON_IN_FUTURE,
    REASON_TOO_OLD,
    ClockRejectionError,
    check_clock_window,
)
from tagpulse.ingestion.service import IngestionService
from tagpulse.models.schemas import TagReadCreate, TagReadResponse


class TestCheckClockWindow:
    def test_in_window_returns_none(self) -> None:
        now = datetime.now(UTC)
        assert check_clock_window(now) is None

    def test_one_hour_old_accepted(self) -> None:
        now = datetime.now(UTC)
        assert check_clock_window(now - timedelta(hours=1)) is None

    def test_too_old_rejected(self) -> None:
        now = datetime.now(UTC)
        assert check_clock_window(now - timedelta(hours=25)) == REASON_TOO_OLD

    def test_in_future_rejected(self) -> None:
        now = datetime.now(UTC)
        assert check_clock_window(now + timedelta(minutes=10)) == REASON_IN_FUTURE

    def test_naive_timestamp_treated_as_utc(self) -> None:
        now = datetime.now(UTC)
        naive = now.replace(tzinfo=None)
        assert check_clock_window(naive) is None

    def test_boundary_24h_minus_one_second_accepted(self) -> None:
        now = datetime.now(UTC)
        assert check_clock_window(now - timedelta(hours=24, seconds=-1)) is None


class _FakeRepo:
    def __init__(self) -> None:
        self.reads: list[TagReadResponse] = []
        self.rejections: list[tuple[object, TagReadCreate, str]] = []

    async def insert(self, tenant_id, read):  # type: ignore[no-untyped-def]
        resp = TagReadResponse(
            id=uuid4(),
            device_id=read.device_id,
            tag_id=read.tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=None,
            created_at=datetime.now(UTC),
        )
        self.reads.append(resp)
        return resp

    async def insert_batch(self, tenant_id, reads):  # type: ignore[no-untyped-def]
        return [await self.insert(tenant_id, r) for r in reads]

    async def record_rejection(self, tenant_id, read, reason):  # type: ignore[no-untyped-def]
        self.rejections.append((tenant_id, read, reason))


@pytest.fixture
def repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture
def service(repo: _FakeRepo) -> IngestionService:
    bus = AsyncEventBus(capacity=100)
    bus.publish = AsyncMock()  # type: ignore[method-assign]
    return IngestionService(repo=repo, event_bus=bus)  # type: ignore[arg-type]


class TestIngestRejectsOutOfWindow:
    @pytest.mark.asyncio
    async def test_too_old_raises_and_records(
        self, service: IngestionService, repo: _FakeRepo
    ) -> None:
        old_ts = datetime.now(UTC) - timedelta(hours=48)
        read = TagReadCreate(device_id=uuid4(), tag_id="T1", timestamp=old_ts)
        with pytest.raises(ClockRejectionError) as exc:
            await service.ingest(uuid4(), read)
        assert exc.value.reason == REASON_TOO_OLD
        assert len(repo.reads) == 0
        assert len(repo.rejections) == 1
        assert repo.rejections[0][2] == REASON_TOO_OLD

    @pytest.mark.asyncio
    async def test_in_future_raises_and_records(
        self, service: IngestionService, repo: _FakeRepo
    ) -> None:
        future_ts = datetime.now(UTC) + timedelta(hours=1)
        read = TagReadCreate(device_id=uuid4(), tag_id="T2", timestamp=future_ts)
        with pytest.raises(ClockRejectionError) as exc:
            await service.ingest(uuid4(), read)
        assert exc.value.reason == REASON_IN_FUTURE

    @pytest.mark.asyncio
    async def test_batch_skips_rejected_and_inserts_rest(
        self, service: IngestionService, repo: _FakeRepo
    ) -> None:
        now = datetime.now(UTC)
        good = TagReadCreate(device_id=uuid4(), tag_id="OK", timestamp=now)
        bad_old = TagReadCreate(device_id=uuid4(), tag_id="OLD", timestamp=now - timedelta(days=2))
        bad_future = TagReadCreate(
            device_id=uuid4(),
            tag_id="FUT",
            timestamp=now + timedelta(minutes=30),
        )
        ingested, rejected = await service.ingest_batch(uuid4(), [good, bad_old, bad_future])
        assert ingested == 1
        assert rejected == 2
        assert len(repo.reads) == 1
        assert {r[2] for r in repo.rejections} == {
            REASON_TOO_OLD,
            REASON_IN_FUTURE,
        }


class TestEventBusUnaffectedByRejection:
    @pytest.mark.asyncio
    async def test_no_publish_on_rejection(self, service: IngestionService) -> None:
        old_ts = datetime.now(UTC) - timedelta(hours=48)
        read = TagReadCreate(device_id=uuid4(), tag_id="T", timestamp=old_ts)
        with pytest.raises(ClockRejectionError):
            await service.ingest(uuid4(), read)
        assert service._event_bus.publish.await_count == 0  # type: ignore[union-attr]
        # Sanity: Topic enum still resolves
        assert Topic.TAG_READ_CREATED is not None


class TestObserveMode:
    """Sprint 16 §10 — observe-mode flag inserts the row + records rejection."""

    @pytest.mark.asyncio
    async def test_observe_mode_inserts_old_event_and_records_rejection(
        self,
        service: IngestionService,
        repo: _FakeRepo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tagpulse.core import config as config_module
        from tagpulse.ingestion import service as service_module

        monkeypatch.setattr(service_module.settings, "ingest_clock_enforce", False, raising=True)
        # Sanity that we hit the same Settings object the service module uses
        assert config_module.settings.ingest_clock_enforce is False

        old_ts = datetime.now(UTC) - timedelta(hours=48)
        read = TagReadCreate(device_id=uuid4(), tag_id="T-OBS", timestamp=old_ts)
        result = await service.ingest(uuid4(), read)

        assert result.tag_id == "T-OBS"
        assert len(repo.reads) == 1
        assert len(repo.rejections) == 1
        assert repo.rejections[0][2] == REASON_TOO_OLD

    @pytest.mark.asyncio
    async def test_observe_mode_batch_inserts_all_records_rejection(
        self,
        service: IngestionService,
        repo: _FakeRepo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tagpulse.ingestion import service as service_module

        monkeypatch.setattr(service_module.settings, "ingest_clock_enforce", False, raising=True)
        now = datetime.now(UTC)
        good = TagReadCreate(device_id=uuid4(), tag_id="OK", timestamp=now)
        bad_old = TagReadCreate(device_id=uuid4(), tag_id="OLD", timestamp=now - timedelta(days=2))
        ingested, rejected = await service.ingest_batch(uuid4(), [good, bad_old])
        assert ingested == 2
        assert rejected == 1
