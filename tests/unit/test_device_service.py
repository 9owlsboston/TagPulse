"""Unit tests for the DeviceService using fake repository."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.device_service import DeviceNotFoundError, DeviceService
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate

TENANT_ID = uuid4()  # shared tenant for all tests


class FakeDeviceRepository:
    """In-memory device repository for unit tests."""

    def __init__(self) -> None:
        self.devices: dict[UUID, DeviceResponse] = {}

    async def create(self, tenant_id: UUID, device: DeviceCreate) -> DeviceResponse:
        now = datetime.now(UTC)
        resp = DeviceResponse(
            id=uuid4(),
            name=device.name,
            device_type=device.device_type,
            status="active",
            metadata=device.metadata,
            configuration=device.configuration,
            firmware_version=device.firmware_version,
            connection_state="unknown",
            last_seen=None,
            created_at=now,
            updated_at=now,
        )
        self.devices[resp.id] = resp
        return resp

    async def get(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse | None:
        return self.devices.get(device_id)

    async def list(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        device_type: str | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]:
        results = list(self.devices.values())
        if status is not None:
            results = [d for d in results if d.status == status]
        if device_type is not None:
            results = [d for d in results if d.device_type == device_type]
        # ``labels`` is accepted but ignored — fakes can't replay the EXISTS
        # join shape. Repo-level filtering is exercised in
        # tests/unit/test_label_filter.py.
        return results[offset : offset + limit]

    async def update(
        self, tenant_id: UUID, device_id: UUID, patch: DeviceUpdate
    ) -> DeviceResponse | None:
        existing = self.devices.get(device_id)
        if existing is None:
            return None
        data = existing.model_dump()
        data.update(patch.model_dump(exclude_unset=True))
        data["updated_at"] = datetime.now(UTC)
        updated = DeviceResponse(**data)
        self.devices[device_id] = updated
        return updated

    async def decommission(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse | None:
        existing = self.devices.get(device_id)
        if existing is None:
            return None
        data = existing.model_dump()
        data["status"] = "decommissioned"
        data["connection_state"] = "offline"
        data["updated_at"] = datetime.now(UTC)
        updated = DeviceResponse(**data)
        self.devices[device_id] = updated
        return updated

    async def update_status(
        self,
        tenant_id: UUID,
        device_id: UUID,
        *,
        connection_state: str,
        firmware_version: str | None = None,
    ) -> DeviceResponse | None:
        existing = self.devices.get(device_id)
        if existing is None:
            return None
        data = existing.model_dump()
        data["connection_state"] = connection_state
        if firmware_version is not None:
            data["firmware_version"] = firmware_version
        data["updated_at"] = datetime.now(UTC)
        updated = DeviceResponse(**data)
        self.devices[device_id] = updated
        return updated

    async def record_last_seen(self, tenant_id: UUID, device_id: UUID, seen_at: datetime) -> None:
        existing = self.devices.get(device_id)
        if existing is not None:
            data = existing.model_dump()
            data["last_seen"] = seen_at
            self.devices[device_id] = DeviceResponse(**data)


@pytest.fixture
def fake_repo() -> FakeDeviceRepository:
    return FakeDeviceRepository()


@pytest.fixture
def service(fake_repo: FakeDeviceRepository) -> DeviceService:
    return DeviceService(repo=fake_repo)


class TestDeviceService:
    async def test_register_device(
        self, service: DeviceService, fake_repo: FakeDeviceRepository
    ) -> None:
        device = DeviceCreate(name="Reader-01", device_type="rfid_reader")
        result = await service.register(TENANT_ID, device)
        assert result.name == "Reader-01"
        assert result.device_type == "rfid_reader"
        assert result.status == "active"
        assert result.connection_state == "unknown"
        assert len(fake_repo.devices) == 1

    async def test_get_device(self, service: DeviceService) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="Reader-02"))
        result = await service.get(TENANT_ID, created.id)
        assert result.id == created.id
        assert result.name == "Reader-02"

    async def test_get_device_not_found(self, service: DeviceService) -> None:
        with pytest.raises(DeviceNotFoundError):
            await service.get(TENANT_ID, uuid4())

    async def test_list_devices(self, service: DeviceService) -> None:
        await service.register(TENANT_ID, DeviceCreate(name="R1"))
        await service.register(TENANT_ID, DeviceCreate(name="R2"))
        await service.register(TENANT_ID, DeviceCreate(name="R3"))
        results = await service.list_devices(TENANT_ID)
        assert len(results) == 3

    async def test_list_devices_filter_status(self, service: DeviceService) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="R1"))
        await service.register(TENANT_ID, DeviceCreate(name="R2"))
        await service.decommission(TENANT_ID, created.id)
        active = await service.list_devices(TENANT_ID, status="active")
        assert len(active) == 1

    async def test_update_device(self, service: DeviceService) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="Old Name"))
        result = await service.update(TENANT_ID, created.id, DeviceUpdate(name="New Name"))
        assert result.name == "New Name"

    async def test_update_device_not_found(self, service: DeviceService) -> None:
        with pytest.raises(DeviceNotFoundError):
            await service.update(TENANT_ID, uuid4(), DeviceUpdate(name="X"))

    async def test_decommission_device(self, service: DeviceService) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="R1"))
        result = await service.decommission(TENANT_ID, created.id)
        assert result.status == "decommissioned"
        assert result.connection_state == "offline"

    async def test_decommission_not_found(self, service: DeviceService) -> None:
        with pytest.raises(DeviceNotFoundError):
            await service.decommission(TENANT_ID, uuid4())

    async def test_update_status(self, service: DeviceService) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="R1"))
        result = await service.update_status(
            TENANT_ID, created.id, connection_state="online", firmware_version="2.1.0"
        )
        assert result.connection_state == "online"
        assert result.firmware_version == "2.1.0"

    async def test_update_status_not_found(self, service: DeviceService) -> None:
        with pytest.raises(DeviceNotFoundError):
            await service.update_status(TENANT_ID, uuid4(), connection_state="online")

    async def test_record_last_seen(
        self, service: DeviceService, fake_repo: FakeDeviceRepository
    ) -> None:
        created = await service.register(TENANT_ID, DeviceCreate(name="R1"))
        now = datetime.now(UTC)
        await service.record_last_seen(TENANT_ID, created.id, now)
        assert fake_repo.devices[created.id].last_seen == now
