"""FastAPI dependency factories for database sessions and services."""

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.api.services.asset_service import AssetService
from tagpulse.api.services.device_service import DeviceService
from tagpulse.api.services.inventory_service import InventoryService
from tagpulse.api.services.query_service import QueryService
from tagpulse.api.services.sites_zones_service import SiteZoneService
from tagpulse.api.services.telemetry_model_service import TelemetryModelService
from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.audit import AuditLogger
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import EventBus
from tagpulse.ingestion.service import IngestionService
from tagpulse.repositories.protocols import (
    DeviceRepository,
    TagReadRepository,
)
from tagpulse.repositories.timescaledb.antennas import TimescaleAntennaRepository
from tagpulse.repositories.timescaledb.devices import TimescaleDeviceRepository
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.repositories.timescaledb.tag_reads import TimescaleTagReadRepository
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)


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


async def get_antenna_repo(
    session: AsyncSession = Depends(get_session),
) -> TimescaleAntennaRepository:
    """Provide a TimescaleAntennaRepository bound to the current session."""
    return TimescaleAntennaRepository(session)


def get_event_bus(request: Request) -> EventBus:
    """Retrieve the EventBus from application state."""
    return request.app.state.event_bus  # type: ignore[no-any-return]


def get_usage_meter_optional(request: Request) -> "UsageMeter | None":
    """Retrieve the UsageMeter from application state if started.

    Returns ``None`` outside the app lifespan (e.g. some tests) so the
    ingestion service falls back to OTel-only counting.
    """
    return getattr(request.app.state, "usage_meter", None)


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
    yield TelemetryModelService(session=session, audit=AuditLogger(session=session))


async def get_telemetry_readings_repo(
    session: AsyncSession = Depends(get_session),
) -> TimescaleTelemetryReadingsRepository:
    """Provide the subject-scoped readings repo bound to the session.

    Sprint 19's ``/telemetry/readings`` and ``/telemetry/aggregates``
    endpoints depend on this directly; the ingest pipeline picks the
    same instance up via :func:`get_ingestion_service`. Sprint 21
    folded the Sprint 14 device-shaped surface into this same repo,
    so :class:`TelemetryService` also takes its instance from here.
    """
    return TimescaleTelemetryReadingsRepository(session)


async def get_telemetry_service(
    repo: TimescaleTelemetryReadingsRepository = Depends(get_telemetry_readings_repo),
    device_repo: DeviceRepository = Depends(get_device_repo),
    event_bus: EventBus = Depends(get_event_bus),
    model_service: TelemetryModelService = Depends(get_telemetry_model_service),
) -> AsyncGenerator[TelemetryService, None]:
    """Provide a TelemetryService wired with repos, event bus, and model lookup."""
    yield TelemetryService(
        repo=repo,
        event_bus=event_bus,
        model_service=model_service,
        device_repo=device_repo,
    )


async def get_ingestion_service(
    repo: TagReadRepository = Depends(get_tag_read_repo),
    device_repo: DeviceRepository = Depends(get_device_repo),
    event_bus: EventBus = Depends(get_event_bus),
    telemetry_service: TelemetryService = Depends(get_telemetry_service),
    session: AsyncSession = Depends(get_session),
    usage_meter: UsageMeter | None = Depends(get_usage_meter_optional),
) -> AsyncGenerator[IngestionService, None]:
    """Provide an IngestionService wired with repo, event bus, and telemetry mirror."""
    from tagpulse.repositories.timescaledb.assets import (
        TimescaleAssetTagBindingRepository,
    )
    from tagpulse.repositories.timescaledb.inventory import (
        TimescaleLotRepository,
        TimescaleProductRepository,
        TimescaleStockItemRepository,
        TimescaleStockMovementRepository,
        TimescaleTagDataMappingRepository,
    )
    from tagpulse.repositories.timescaledb.sites_zones import (
        TimescaleZoneRepository,
    )
    from tagpulse.repositories.timescaledb.tags import (
        TimescaleTagRepository,
    )
    from tagpulse.repositories.timescaledb.telemetry import (
        TimescaleTelemetryReadingsRepository,
    )
    from tagpulse.repositories.timescaledb.tenants import (
        TimescaleTenantRepository,
    )

    yield IngestionService(
        repo=repo,
        event_bus=event_bus,
        device_repo=device_repo,
        telemetry_service=telemetry_service,
        binding_repo=TimescaleAssetTagBindingRepository(session),
        zone_repo=TimescaleZoneRepository(session),
        product_repo=TimescaleProductRepository(session),
        lot_repo=TimescaleLotRepository(session),
        stock_repo=TimescaleStockItemRepository(session),
        movement_repo=TimescaleStockMovementRepository(session),
        tag_data_mapping_repo=TimescaleTagDataMappingRepository(session),
        tenant_repo=TimescaleTenantRepository(session),
        telemetry_readings_repo=TimescaleTelemetryReadingsRepository(session),
        tag_repo=TimescaleTagRepository(session),
        usage_meter=usage_meter,
    )


async def get_site_zone_service(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[SiteZoneService, None]:
    """Provide a SiteZoneService bound to the current session."""
    from tagpulse.repositories.timescaledb.sites_zones import (
        TimescaleSiteRepository,
        TimescaleZoneRepository,
    )

    audit = AuditLogger(session=session)
    yield SiteZoneService(
        site_repo=TimescaleSiteRepository(session),
        zone_repo=TimescaleZoneRepository(session),
        audit=audit,
    )


async def get_asset_service(
    session: AsyncSession = Depends(get_session),
    event_bus: EventBus = Depends(get_event_bus),
) -> AsyncGenerator["AssetService", None]:
    """Provide an AssetService bound to the current session."""
    from tagpulse.repositories.timescaledb.asset_location import (
        TimescaleAssetLocationRepository,
    )
    from tagpulse.repositories.timescaledb.assets import (
        TimescaleAssetRepository,
        TimescaleAssetTagBindingRepository,
    )
    from tagpulse.repositories.timescaledb.external_locations import (
        TimescaleExternalLocationRepository,
    )
    from tagpulse.repositories.timescaledb.tenants import (
        TimescaleTenantRepository,
    )

    yield AssetService(
        asset_repo=TimescaleAssetRepository(session),
        binding_repo=TimescaleAssetTagBindingRepository(session),
        audit=AuditLogger(session=session),
        external_location_repo=TimescaleExternalLocationRepository(session),
        event_bus=event_bus,
        asset_location_repo=TimescaleAssetLocationRepository(session),
        telemetry_readings_repo=TimescaleTelemetryReadingsRepository(session),
        tenant_repo=TimescaleTenantRepository(session),
    )


async def get_inventory_service(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[InventoryService, None]:
    """Provide an InventoryService bound to the current session."""
    from tagpulse.repositories.timescaledb.inventory import (
        TimescaleLotRepository,
        TimescaleProductRepository,
        TimescaleStockItemRepository,
        TimescaleStockMovementRepository,
        TimescaleTagDataMappingRepository,
    )
    from tagpulse.repositories.timescaledb.tenants import (
        TimescaleTenantRepository,
    )

    yield InventoryService(
        product_repo=TimescaleProductRepository(session),
        lot_repo=TimescaleLotRepository(session),
        stock_repo=TimescaleStockItemRepository(session),
        movement_repo=TimescaleStockMovementRepository(session),
        mapping_repo=TimescaleTagDataMappingRepository(session),
        audit=AuditLogger(session=session),
        telemetry_readings_repo=TimescaleTelemetryReadingsRepository(session),
        tenant_repo=TimescaleTenantRepository(session),
    )
