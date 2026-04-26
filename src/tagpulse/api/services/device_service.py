"""Device registry service — business logic for device CRUD and status."""

import logging
import uuid as uuid_mod
from datetime import UTC, datetime
from uuid import UUID

from tagpulse.core.audit import AuditLogger
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from tagpulse.repositories.protocols import DeviceRepository

logger = logging.getLogger(__name__)


class DeviceNotFoundError(Exception):
    """Raised when a device is not found by ID."""

    def __init__(self, device_id: UUID) -> None:
        self.device_id = device_id
        super().__init__(f"Device not found: {device_id}")


class DeviceService:
    """Manages device registration, configuration, and status tracking."""

    def __init__(
        self,
        repo: DeviceRepository,
        event_bus: EventBus | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self._repo = repo
        self._event_bus = event_bus
        self._audit = audit

    async def register(self, tenant_id: UUID, device: DeviceCreate) -> DeviceResponse:
        """Register a new device (reader)."""
        result = await self._repo.create(tenant_id, device)
        logger.info(
            "Device registered: id=%s name=%s type=%s",
            result.id, result.name, result.device_type,
        )
        if self._event_bus:
            await self._event_bus.publish(
                Topic.DEVICE_REGISTERED,
                Event(
                    id=uuid_mod.uuid4(),
                    topic=Topic.DEVICE_REGISTERED,
                    timestamp=datetime.now(UTC),
                    payload={
                        "tenant_id": str(tenant_id),
                        "device_id": str(result.id),
                        "name": result.name,
                        "device_type": result.device_type,
                    },
                ),
            )
        if self._audit:
            await self._audit.log(
                tenant_id, "created", "device", result.id,
                {"name": result.name, "device_type": result.device_type},
            )
        return result

    async def get(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse:
        """Get a device by ID. Raises DeviceNotFoundError if not found."""
        result = await self._repo.get(tenant_id, device_id)
        if result is None:
            raise DeviceNotFoundError(device_id)
        return result

    async def list_devices(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        device_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeviceResponse]:
        """List devices with optional filters."""
        return await self._repo.list(
            tenant_id, status=status, device_type=device_type, limit=limit, offset=offset
        )

    async def update(self, tenant_id: UUID, device_id: UUID, patch: DeviceUpdate) -> DeviceResponse:
        """Update device fields. Raises DeviceNotFoundError if not found."""
        result = await self._repo.update(tenant_id, device_id, patch)
        if result is None:
            raise DeviceNotFoundError(device_id)
        logger.info("Device updated: id=%s", device_id)
        if self._audit:
            await self._audit.log(
                tenant_id, "updated", "device", device_id,
                patch.model_dump(exclude_unset=True),
            )
        return result

    async def decommission(self, tenant_id: UUID, device_id: UUID) -> DeviceResponse:
        """Decommission a device. Raises DeviceNotFoundError if not found."""
        result = await self._repo.decommission(tenant_id, device_id)
        if result is None:
            raise DeviceNotFoundError(device_id)
        logger.info("Device decommissioned: id=%s", device_id)
        if self._event_bus:
            await self._event_bus.publish(
                Topic.DEVICE_DECOMMISSIONED,
                Event(
                    id=uuid_mod.uuid4(),
                    topic=Topic.DEVICE_DECOMMISSIONED,
                    timestamp=datetime.now(UTC),
                    payload={
                        "tenant_id": str(tenant_id),
                        "device_id": str(device_id),
                    },
                ),
            )
        if self._audit:
            await self._audit.log(
                tenant_id, "decommissioned", "device", device_id,
            )
        return result

    async def update_status(
        self,
        tenant_id: UUID,
        device_id: UUID,
        *,
        connection_state: str,
        firmware_version: str | None = None,
    ) -> DeviceResponse:
        """Update device connection state and firmware."""
        result = await self._repo.update_status(
            tenant_id, device_id,
            connection_state=connection_state,
            firmware_version=firmware_version,
        )
        if result is None:
            raise DeviceNotFoundError(device_id)
        logger.info("Device status updated: id=%s state=%s", device_id, connection_state)
        return result

    async def record_last_seen(self, tenant_id: UUID, device_id: UUID, seen_at: datetime) -> None:
        """Record when a device was last seen."""
        await self._repo.record_last_seen(tenant_id, device_id, seen_at)
