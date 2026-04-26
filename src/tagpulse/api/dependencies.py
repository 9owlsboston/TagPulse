"""FastAPI dependency factories for database sessions and services."""

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.services.device_service import DeviceService
from tagpulse.api.services.query_service import QueryService
from tagpulse.api.services.telemetry_model_service import TelemetryModelService
from tagpulse.core.audit import AuditLogger
from tagpulse.events.protocol import EventBus
from tagpulse.ingestion.service import IngestionService
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository


async def get_tag_read_repo(
    session: AsyncSession = Depends(get_session),
) -> TagReadRepository:
    """Provide a TagReadRepository bound to the current session."""
    return TimescaleTagReadRepository(session)


async def get_device_repo(
    session: AsyncSession = Depends(get_session),
) -> DeviceRepository:
    """Provide a DeviceRepository bound to the current session."""
    return TimescaleDeviceRepository(session)


def get_event_bus(request: Request) -> EventBus:
    """Retrieve the EventBus from application state."""
    return request.app.state.event_bus  # type: ignore[no-any-return]


async def get_ingestion_service(
    repo: TagReadRepository = Depends(get_tag_read_repo),
    event_bus: EventBus = Depends(get_event_bus),
) -> AsyncGenerator[IngestionService, None]:
    """Provide an IngestionService wired with repo and event bus."""
    yield IngestionService(repo=repo, event_bus=event_bus)


async def get_device_service(
    repo: DeviceRepository = Depends(get_device_repo),
    event_bus: EventBus = Depends(get_event_bus),
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[DeviceService, None]:
    """Provide a DeviceService wired with repo, event bus, and audit logger."""
    audit = AuditLogger(session=session)
    yield DeviceService(repo=repo, event_bus=event_bus, audit=audit)


async def get_query_service(
    tag_read_repo: TagReadRepository = Depends(get_tag_read_repo),
    device_repo: DeviceRepository = Depends(get_device_repo),
) -> AsyncGenerator[QueryService, None]:
    """Provide a QueryService wired with repos."""
    yield QueryService(tag_read_repo=tag_read_repo, device_repo=device_repo)


async def get_telemetry_model_service(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[TelemetryModelService, None]:
    """Provide a TelemetryModelService bound to the current session."""
    yield TelemetryModelService(session=session)
