"""Unit tests for the QueryService using fake repositories."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.query_service import QueryService
from tagpulse.models.schemas import (
    DeviceResponse,
    ReadsPerHour,
    TagReadCreate,
    TagReadResponse,
    UniqueTagsPerWindow,
    ZoneResponse,
)

TENANT_ID = uuid4()


class FakeTagReadRepo:
    """In-memory tag read repository for query tests."""

    def __init__(self) -> None:
        self.reads: list[TagReadResponse] = []

    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagReadResponse:
        now = datetime.now(UTC)
        resp = TagReadResponse(
            id=uuid4(),
            device_id=read.device_id,
            tag_id=read.tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            created_at=now,
        )
        self.reads.append(resp)
        return resp

    async def insert_batch(
        self, tenant_id: UUID, reads: list[TagReadCreate]
    ) -> list[TagReadResponse]:
        return [await self.insert(tenant_id, r) for r in reads]

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        tag_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        has_location: bool | None = None,
        epc_scheme: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagReadResponse]:
        results = list(self.reads)
        if device_id is not None:
            results = [r for r in results if r.device_id == device_id]
        if tag_id is not None:
            results = [r for r in results if r.tag_id == tag_id]
        if start is not None:
            results = [r for r in results if r.timestamp >= start]
        if end is not None:
            results = [r for r in results if r.timestamp <= end]
        if has_location is True:
            results = [r for r in results if r.latitude is not None]
        elif has_location is False:
            results = [r for r in results if r.latitude is None]
        if epc_scheme is not None:
            results = [r for r in results if r.epc_scheme == epc_scheme]
        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results[offset : offset + limit]

    async def reads_per_hour(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        bucket_minutes: int = 60,
    ) -> list[ReadsPerHour]:
        filtered = list(self.reads)
        if device_id is not None:
            filtered = [r for r in filtered if r.device_id == device_id]
        if start is not None:
            filtered = [r for r in filtered if r.timestamp >= start]
        if end is not None:
            filtered = [r for r in filtered if r.timestamp <= end]
        buckets: dict[tuple[datetime, UUID], int] = {}
        width = bucket_minutes * 60
        for r in filtered:
            if bucket_minutes == 60:
                bucket = r.timestamp.replace(minute=0, second=0, microsecond=0)
            else:
                floored = (r.timestamp.timestamp() // width) * width
                bucket = datetime.fromtimestamp(floored, tz=r.timestamp.tzinfo or UTC)
            key = (bucket, r.device_id)
            buckets[key] = buckets.get(key, 0) + 1
        return [ReadsPerHour(bucket=k[0], device_id=k[1], read_count=v) for k, v in buckets.items()]

    async def unique_tags_per_window(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        window_minutes: int = 60,
    ) -> list[UniqueTagsPerWindow]:
        filtered = list(self.reads)
        if device_id is not None:
            filtered = [r for r in filtered if r.device_id == device_id]
        buckets: dict[tuple[datetime, UUID], set[str]] = {}
        for r in filtered:
            bucket = r.timestamp.replace(minute=0, second=0, microsecond=0)
            key = (bucket, r.device_id)
            buckets.setdefault(key, set()).add(r.tag_id)
        return [
            UniqueTagsPerWindow(bucket=k[0], device_id=k[1], unique_tags=len(v))
            for k, v in buckets.items()
        ]

    async def count_reads_since(self, tenant_id: UUID, device_id: UUID, since: datetime) -> int:
        return len([r for r in self.reads if r.device_id == device_id and r.timestamp >= since])

    async def count_alerts_since(self, tenant_id: UUID, device_id: UUID, since: datetime) -> int:
        return 0


class FakeDeviceRepo:
    """In-memory device repository for query tests."""

    def __init__(self) -> None:
        self.devices: dict[UUID, DeviceResponse] = {}

    def add(self, **kwargs: object) -> DeviceResponse:
        now = datetime.now(UTC)
        defaults: dict[str, object] = {
            "id": uuid4(),
            "name": "Reader",
            "device_type": "rfid_reader",
            "status": "active",
            "metadata": None,
            "configuration": None,
            "firmware_version": None,
            "connection_state": "online",
            "last_seen": now,
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(kwargs)
        device = DeviceResponse(**defaults)  # type: ignore[arg-type]
        self.devices[device.id] = device
        return device

    async def get(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse | None:
        return self.devices.get(device_id)

    async def list(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        device_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]:
        results = list(self.devices.values())
        if status is not None:
            results = [d for d in results if d.status == status]
        return results[:limit]


@pytest.fixture
def tag_repo() -> FakeTagReadRepo:
    return FakeTagReadRepo()


@pytest.fixture
def device_repo() -> FakeDeviceRepo:
    return FakeDeviceRepo()


@pytest.fixture
def service(tag_repo: FakeTagReadRepo, device_repo: FakeDeviceRepo) -> QueryService:
    return QueryService(tag_read_repo=tag_repo, device_repo=device_repo)


class TestQueryTagReads:
    async def test_query_all(self, service: QueryService, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        now = datetime.now(UTC)
        for i in range(5):
            await tag_repo.insert(
                TENANT_ID,
                TagReadCreate(
                    device_id=did, tag_id=f"TAG{i}", timestamp=now - timedelta(minutes=i)
                ),
            )
        results = await service.query_tag_reads(TENANT_ID)
        assert len(results) == 5

    async def test_query_filter_device(
        self, service: QueryService, tag_repo: FakeTagReadRepo
    ) -> None:
        d1, d2 = uuid4(), uuid4()
        now = datetime.now(UTC)
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=d1, tag_id="A", timestamp=now))
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=d2, tag_id="B", timestamp=now))
        results = await service.query_tag_reads(TENANT_ID, device_id=d1)
        assert len(results) == 1
        assert results[0].device_id == d1

    async def test_query_filter_tag(self, service: QueryService, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        now = datetime.now(UTC)
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="A", timestamp=now))
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="B", timestamp=now))
        results = await service.query_tag_reads(TENANT_ID, tag_id="A")
        assert len(results) == 1

    async def test_query_filter_time_range(
        self, service: QueryService, tag_repo: FakeTagReadRepo
    ) -> None:
        did = uuid4()
        now = datetime.now(UTC)
        await tag_repo.insert(
            TENANT_ID, TagReadCreate(device_id=did, tag_id="A", timestamp=now - timedelta(hours=3))
        )
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="B", timestamp=now))
        results = await service.query_tag_reads(TENANT_ID, start=now - timedelta(hours=1))
        assert len(results) == 1

    async def test_query_pagination(self, service: QueryService, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        now = datetime.now(UTC)
        for i in range(10):
            await tag_repo.insert(
                TENANT_ID,
                TagReadCreate(device_id=did, tag_id=f"T{i}", timestamp=now - timedelta(minutes=i)),
            )
        page1 = await service.query_tag_reads(TENANT_ID, limit=3, offset=0)
        page2 = await service.query_tag_reads(TENANT_ID, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0].tag_id != page2[0].tag_id


class FakeZoneRepo:
    """Resolves a reader_bound zone per device, counting lookups."""

    def __init__(self, mapping: dict[UUID, tuple[UUID, str]]) -> None:
        self._map = mapping
        self.calls = 0

    async def get_zone_for_reader(self, tenant_id: UUID, device_id: UUID) -> ZoneResponse | None:
        self.calls += 1
        entry = self._map.get(device_id)
        if entry is None:
            return None
        zid, zname = entry
        now = datetime.now(UTC)
        return ZoneResponse(
            id=zid,
            tenant_id=tenant_id,
            site_id=uuid4(),
            name=zname,
            kind="reader_bound",
            fixed_reader_ids=[device_id],
            polygon_geojson=None,
            metadata=None,
            created_at=now,
            updated_at=now,
        )


def _floor_read(device_id: UUID) -> TagReadResponse:
    now = datetime.now(UTC)
    return TagReadResponse(
        id=uuid4(),
        device_id=device_id,
        tag_id="A",
        timestamp=now,
        signal_strength=None,
        sensor_data=None,
        created_at=now,
    )


class TestLocationDescriptor:
    async def test_geo_read_gets_geo_descriptor(self, tag_repo: FakeTagReadRepo) -> None:
        service = QueryService(tag_read_repo=tag_repo, device_repo=FakeDeviceRepo())
        now = datetime.now(UTC)
        tag_repo.reads.append(
            TagReadResponse(
                id=uuid4(),
                device_id=uuid4(),
                tag_id="A",
                timestamp=now,
                signal_strength=None,
                sensor_data=None,
                latitude=47.6,
                longitude=-122.3,
                location_accuracy_m=5.0,
                location_source="gps",
                created_at=now,
            )
        )
        results = await service.query_tag_reads(TENANT_ID)
        loc = results[0].location
        assert loc is not None
        assert loc.kind == "geo"
        assert loc.lat == 47.6
        assert loc.lon == -122.3
        assert loc.accuracy_m == 5.0
        assert loc.source == "gps"

    async def test_fixed_read_resolves_zone(self, tag_repo: FakeTagReadRepo) -> None:
        did, zid = uuid4(), uuid4()
        zone_repo = FakeZoneRepo({did: (zid, "Dock A")})
        service = QueryService(
            tag_read_repo=tag_repo, device_repo=FakeDeviceRepo(), zone_repo=zone_repo
        )
        tag_repo.reads.append(_floor_read(did))
        results = await service.query_tag_reads(TENANT_ID)
        loc = results[0].location
        assert loc is not None
        assert loc.kind == "floor"
        assert loc.zone_id == zid
        assert loc.zone_name == "Dock A"

    async def test_fixed_read_without_zone_is_none(self, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        zone_repo = FakeZoneRepo({})
        service = QueryService(
            tag_read_repo=tag_repo, device_repo=FakeDeviceRepo(), zone_repo=zone_repo
        )
        tag_repo.reads.append(_floor_read(did))
        results = await service.query_tag_reads(TENANT_ID)
        assert results[0].location is not None
        assert results[0].location.kind == "none"

    async def test_no_zone_repo_yields_none_kind(self, tag_repo: FakeTagReadRepo) -> None:
        # No zone repo wired → floor reads can't resolve a zone.
        service = QueryService(tag_read_repo=tag_repo, device_repo=FakeDeviceRepo())
        tag_repo.reads.append(_floor_read(uuid4()))
        results = await service.query_tag_reads(TENANT_ID)
        assert results[0].location is not None
        assert results[0].location.kind == "none"

    async def test_zone_lookup_cached_per_device(self, tag_repo: FakeTagReadRepo) -> None:
        did, zid = uuid4(), uuid4()
        zone_repo = FakeZoneRepo({did: (zid, "Dock A")})
        service = QueryService(
            tag_read_repo=tag_repo, device_repo=FakeDeviceRepo(), zone_repo=zone_repo
        )
        # Three reads from the same device → one zone lookup.
        for _ in range(3):
            tag_repo.reads.append(_floor_read(did))
        results = await service.query_tag_reads(TENANT_ID)
        assert len(results) == 3
        assert all(r.location is not None and r.location.kind == "floor" for r in results)
        assert zone_repo.calls == 1

    async def test_floor_resolver_preferred_over_reader_bound(
        self, tag_repo: FakeTagReadRepo
    ) -> None:
        from dataclasses import dataclass

        did, reader_bound_zone, floor_zone = uuid4(), uuid4(), uuid4()

        @dataclass
        class _FloorRef:
            id: UUID
            name: str

        class _FloorResolver:
            async def resolve(
                self, tenant_id: UUID, device_id: UUID, reader_antenna: int | None
            ) -> _FloorRef:
                return _FloorRef(id=floor_zone, name="Bay A (floor)")

        zone_repo = FakeZoneRepo({did: (reader_bound_zone, "Dock A")})
        service = QueryService(
            tag_read_repo=tag_repo,
            device_repo=FakeDeviceRepo(),
            zone_repo=zone_repo,
            floor_resolver=_FloorResolver(),  # type: ignore[arg-type]
        )
        tag_repo.reads.append(_floor_read(did))
        results = await service.query_tag_reads(TENANT_ID)
        loc = results[0].location
        assert loc is not None
        assert loc.kind == "floor"
        # Floor resolver wins; reader_bound was never consulted.
        assert loc.zone_id == floor_zone
        assert loc.zone_name == "Bay A (floor)"
        assert zone_repo.calls == 0


class TestAggregations:
    async def test_reads_per_hour(self, service: QueryService, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
        for i in range(5):
            await tag_repo.insert(
                TENANT_ID, TagReadCreate(device_id=did, tag_id=f"T{i}", timestamp=now)
            )
        result = await service.reads_per_hour(TENANT_ID, device_id=did)
        assert len(result) == 1
        assert result[0].read_count == 5

    async def test_reads_per_hour_custom_bucket_splits_sub_hour(
        self, service: QueryService, tag_repo: FakeTagReadRepo
    ) -> None:
        did = uuid4()
        base = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        # Two reads 20 minutes apart fall in the same hour but different 15-min buckets.
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="A", timestamp=base))
        await tag_repo.insert(
            TENANT_ID,
            TagReadCreate(device_id=did, tag_id="B", timestamp=base + timedelta(minutes=20)),
        )
        hourly = await service.reads_per_hour(TENANT_ID, device_id=did)
        assert len(hourly) == 1
        fine = await service.reads_per_hour(TENANT_ID, device_id=did, bucket_minutes=15)
        assert len(fine) == 2
        assert {r.read_count for r in fine} == {1}

    async def test_unique_tags(self, service: QueryService, tag_repo: FakeTagReadRepo) -> None:
        did = uuid4()
        now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="A", timestamp=now))
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="A", timestamp=now))
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=did, tag_id="B", timestamp=now))
        result = await service.unique_tags_per_window(TENANT_ID, device_id=did)
        assert len(result) == 1
        assert result[0].unique_tags == 2


class TestRecentReads:
    async def test_recent_reads_for_device(
        self, service: QueryService, tag_repo: FakeTagReadRepo
    ) -> None:
        did = uuid4()
        now = datetime.now(UTC)
        for i in range(10):
            await tag_repo.insert(
                TENANT_ID,
                TagReadCreate(device_id=did, tag_id=f"T{i}", timestamp=now - timedelta(minutes=i)),
            )
        results = await service.recent_reads(TENANT_ID, did, limit=5)
        assert len(results) == 5
        assert results[0].timestamp > results[-1].timestamp


class TestDeviceHealth:
    async def test_device_health_list(
        self,
        service: QueryService,
        device_repo: FakeDeviceRepo,
        tag_repo: FakeTagReadRepo,
    ) -> None:
        d1 = device_repo.add(name="R1", connection_state="online")
        d2 = device_repo.add(name="R2", connection_state="offline")
        now = datetime.now(UTC)
        await tag_repo.insert(TENANT_ID, TagReadCreate(device_id=d1.id, tag_id="X", timestamp=now))
        results = await service.device_health(TENANT_ID)
        assert len(results) == 2
        r1 = next(r for r in results if r.device_id == d1.id)
        r2 = next(r for r in results if r.device_id == d2.id)
        assert r1.reads_last_hour == 1
        assert r1.connection_state == "online"
        assert r2.reads_last_hour == 0

    async def test_single_device_health(
        self,
        service: QueryService,
        device_repo: FakeDeviceRepo,
    ) -> None:
        d = device_repo.add(name="R1")
        result = await service.single_device_health(TENANT_ID, d.id)
        assert result is not None
        assert result.device_id == d.id

    async def test_single_device_not_found(self, service: QueryService) -> None:
        result = await service.single_device_health(TENANT_ID, uuid4())
        assert result is None
