"""Asset & tag-binding service (Sprint 15 Phase B + Phase C carrier ops)."""

from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import UTC, datetime
from uuid import UUID

from tagpulse.core.audit import AuditLogger
from tagpulse.core.otel_metrics import (
    asset_load_counter,
    external_locations_counter,
    tag_collisions_global_counter,
)
from tagpulse.core.telemetry_caches import (
    LATEST_TELEMETRY_CACHE,
    SUBJECT_KINDS_CACHE,
)
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import (
    AssetCreate,
    AssetCurrentLocation,
    AssetInZoneSummary,
    AssetPathPoint,
    AssetResponse,
    AssetStateResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUpdate,
    ExternalLocationCreate,
    ExternalLocationResponse,
    FloorPositionCreate,
    FloorPositionResponse,
    ManifestEntry,
    ManifestResponse,
)
from tagpulse.repositories.timescaledb.asset_location import (
    TimescaleAssetLocationRepository,
)
from tagpulse.repositories.timescaledb.asset_positions import (
    TimescaleAssetPositionRepository,
)
from tagpulse.repositories.timescaledb.asset_state import (
    TimescaleAssetStateRepository,
)
from tagpulse.repositories.timescaledb.assets import (
    TimescaleAssetRepository,
    TimescaleAssetTagBindingRepository,
)
from tagpulse.repositories.timescaledb.external_locations import (
    TimescaleExternalLocationRepository,
)
from tagpulse.repositories.timescaledb.sites_zones import (
    TimescaleSiteRepository,
)
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)
from tagpulse.repositories.timescaledb.tenants import TimescaleTenantRepository

logger = logging.getLogger(__name__)


class AssetNotFoundError(Exception):
    """Raised when the asset is not present in the caller's tenant."""


class AssetPositionSiteError(Exception):
    """Raised when a floor position references a site not in the caller's tenant."""

    def __init__(self, site_id: UUID) -> None:
        self.site_id = site_id
        super().__init__(f"Site {site_id} not found in tenant")


class AssetService:
    """CRUD operations for assets and tag bindings, with audit + event hooks."""

    def __init__(
        self,
        asset_repo: TimescaleAssetRepository,
        binding_repo: TimescaleAssetTagBindingRepository,
        audit: AuditLogger,
        external_location_repo: TimescaleExternalLocationRepository | None = None,
        event_bus: EventBus | None = None,
        asset_location_repo: TimescaleAssetLocationRepository | None = None,
        telemetry_readings_repo: (TimescaleTelemetryReadingsRepository | None) = None,
        tenant_repo: TimescaleTenantRepository | None = None,
        position_repo: TimescaleAssetPositionRepository | None = None,
        site_repo: TimescaleSiteRepository | None = None,
        asset_state_repo: TimescaleAssetStateRepository | None = None,
    ) -> None:
        self._assets = asset_repo
        self._bindings = binding_repo
        self._audit = audit
        self._external = external_location_repo
        self._event_bus = event_bus
        self._asset_location = asset_location_repo
        self._telemetry_readings = telemetry_readings_repo
        self._tenant_repo = tenant_repo
        self._positions = position_repo
        self._sites = site_repo
        self._asset_state = asset_state_repo

    # -- Assets --

    async def create_asset(
        self, tenant_id: UUID, user_id: UUID | None, payload: AssetCreate
    ) -> AssetResponse:
        asset = await self._assets.create(tenant_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.created",
            resource_type="asset",
            resource_id=asset.id,
            changes={"name": asset.name, "category_id": str(asset.category_id)},
        )
        return asset

    async def get_asset(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        with_latest_telemetry: bool = False,
    ) -> AssetResponse | None:
        """Fetch a single asset.

        Sprint 19: pass ``with_latest_telemetry=True`` from the
        ``GET /assets/{id}`` route to embed the latest telemetry per
        metric (capped at 5). Skipped silently when the tenant has not
        opted into ``subject_kind='asset'`` telemetry, when the
        readings repo is not wired, or when no readings exist yet.
        """
        asset = await self._assets.get(tenant_id, asset_id)
        if asset is None or not with_latest_telemetry:
            return asset
        if self._telemetry_readings is None or self._tenant_repo is None:
            return asset
        kinds = SUBJECT_KINDS_CACHE.get(tenant_id)
        if kinds is None:
            kinds = tuple(await self._tenant_repo.get_telemetry_subject_kinds(tenant_id))
            SUBJECT_KINDS_CACHE.set(tenant_id, kinds)
        if "asset" not in kinds:
            return asset
        cache_key = (tenant_id, "asset", asset_id)
        latest = LATEST_TELEMETRY_CACHE.get(cache_key)
        if latest is None:
            latest = await self._telemetry_readings.latest_per_metric(
                tenant_id=tenant_id,
                subject_kind="asset",
                subject_id=asset_id,
                limit=5,
            )
            LATEST_TELEMETRY_CACHE.set(cache_key, latest)
        return asset.model_copy(update={"latest_telemetry": latest})

    async def get_asset_state(self, tenant_id: UUID, asset_id: UUID) -> AssetStateResponse | None:
        """Latest fused asset-state snapshot (Sprint 71, ADR-034), or ``None``.

        ``None`` when the asset has no snapshot yet (consolidation not enabled,
        or no reads in the window). The route returns 404 only when the asset
        itself does not exist.
        """
        if self._asset_state is None:
            return None
        return await self._asset_state.latest(tenant_id, asset_id)

    async def get_asset_state_history(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AssetStateResponse]:
        """Fused asset-state snapshots, newest-first (the "was" timeline)."""
        if self._asset_state is None:
            return []
        return await self._asset_state.history(tenant_id, asset_id, since=since, limit=limit)

    async def list_assets(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        category_id: UUID | None = None,
        category_ids: list[UUID] | None = None,
        q: str | None = None,
        labels: dict[str, list[str]] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetResponse]:
        # Sprint 42: collapse the (legacy ``category_id``, new ``category_ids``)
        # pair into a single deduplicated list before reaching the repo.
        # Empty list => no filter (same as ``None``) so the repo can keep a
        # single code path.
        effective: list[UUID] | None
        if category_ids or category_id is not None:
            seen: set[UUID] = set()
            effective = []
            for cid in (*(category_ids or ()), *((category_id,) if category_id else ())):
                if cid in seen:
                    continue
                seen.add(cid)
                effective.append(cid)
        else:
            effective = None
        return await self._assets.list(
            tenant_id,
            status=status,
            category_ids=effective,
            q=q,
            labels=labels,
            limit=limit,
            offset=offset,
        )

    async def update_asset(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        patch: AssetUpdate,
    ) -> AssetResponse | None:
        asset = await self._assets.update(tenant_id, asset_id, patch)
        if asset is not None:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.updated",
                resource_type="asset",
                resource_id=asset.id,
                changes=patch.model_dump(exclude_unset=True),
            )
        return asset

    async def retire_asset(self, tenant_id: UUID, user_id: UUID | None, asset_id: UUID) -> bool:
        deleted = await self._assets.delete(tenant_id, asset_id)
        if deleted:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.retired",
                resource_type="asset",
                resource_id=asset_id,
            )
        return deleted

    # -- Bindings --

    async def bind_tag(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        payload: AssetTagBindingCreate,
    ) -> AssetTagBindingResponse:
        binding = await self._bindings.create(tenant_id, asset_id, payload)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.bound",
            resource_type="asset",
            resource_id=asset_id,
            changes={
                "binding_value": binding.binding_value,
                "binding_kind": binding.binding_kind,
            },
        )
        return binding

    async def list_bindings(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        active_only: bool = False,
    ) -> list[AssetTagBindingResponse]:
        return await self._bindings.list_for_asset(tenant_id, asset_id, active_only=active_only)

    async def unbind_tag(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        binding_value: str,
    ) -> bool:
        unbound = await self._bindings.unbind(tenant_id, asset_id, binding_value)
        if unbound:
            await self._audit.log(
                tenant_id=tenant_id,
                user_id=user_id,
                action="asset.unbound",
                resource_type="asset",
                resource_id=asset_id,
                changes={"binding_value": binding_value},
            )
        return unbound

    async def get_active_binding(
        self, tenant_id: UUID, binding_value: str
    ) -> AssetTagBindingResponse | None:
        """Ingestion hot-path lookup."""
        return await self._bindings.get_active_by_value(tenant_id, binding_value)

    # -- Admin --

    async def count_other_tenant_collisions(self, tenant_id: UUID, binding_value: str) -> int:
        count = await self._bindings.count_other_tenant_collisions(tenant_id, binding_value)
        tag_collisions_global_counter.add(
            1,
            {
                "tenant_id": str(tenant_id),
                "had_collision": "true" if count > 0 else "false",
            },
        )
        return count

    # -- Carrier semantics (Phase C) --

    async def load_onto_carrier(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        parent_asset_id: UUID,
        at: datetime | None = None,
    ) -> AssetResponse:
        """Attach `asset_id` to `parent_asset_id`. Idempotent."""
        if asset_id == parent_asset_id:
            raise ValueError("asset cannot be its own parent")
        # Verify parent exists in same tenant.
        parent = await self._assets.get(tenant_id, parent_asset_id)
        if parent is None:
            raise AssetNotFoundError(parent_asset_id)
        # Multi-step cycle guard: walking the proposed parent's ancestry
        # must not encounter ``asset_id``. Without this, ``set_parent``
        # would happily form A→B→A loops which then hang the recursive CTE
        # in ``get_descendants`` (and thus ``GET /assets/{id}/manifest``).
        await self._assert_no_parent_cycle(tenant_id, asset_id, parent_asset_id)
        result = await self._assets.set_parent(tenant_id, asset_id, parent_asset_id)
        if result is None:
            raise AssetNotFoundError(asset_id)
        updated, prior = result
        if prior == parent_asset_id:
            # Idempotent: already attached to this carrier.
            return updated
        timestamp = at or datetime.now(UTC)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.loaded",
            resource_type="asset",
            resource_id=asset_id,
            changes={
                "parent_asset_id": str(parent_asset_id),
                "prior_parent_asset_id": str(prior) if prior else None,
            },
        )
        asset_load_counter.add(1, {"tenant_id": str(tenant_id), "op": "load"})
        if self._event_bus is not None:
            await self._event_bus.publish(
                Topic.ASSET_LOADED,
                Event(
                    id=uuid_mod.uuid4(),
                    topic=Topic.ASSET_LOADED,
                    timestamp=timestamp,
                    payload={
                        "tenant_id": str(tenant_id),
                        "asset_id": str(asset_id),
                        "parent_asset_id": str(parent_asset_id),
                        "at": timestamp.isoformat(),
                    },
                ),
            )
        return updated

    async def unload_from_carrier(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        at: datetime | None = None,
    ) -> AssetResponse:
        """Detach `asset_id` from its current carrier. Idempotent."""
        result = await self._assets.set_parent(tenant_id, asset_id, None)
        if result is None:
            raise AssetNotFoundError(asset_id)
        updated, prior = result
        if prior is None:
            return updated
        timestamp = at or datetime.now(UTC)
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.unloaded",
            resource_type="asset",
            resource_id=asset_id,
            changes={"prior_parent_asset_id": str(prior)},
        )
        asset_load_counter.add(1, {"tenant_id": str(tenant_id), "op": "unload"})
        if self._event_bus is not None:
            await self._event_bus.publish(
                Topic.ASSET_UNLOADED,
                Event(
                    id=uuid_mod.uuid4(),
                    topic=Topic.ASSET_UNLOADED,
                    timestamp=timestamp,
                    payload={
                        "tenant_id": str(tenant_id),
                        "asset_id": str(asset_id),
                        "prior_parent_asset_id": str(prior),
                        "at": timestamp.isoformat(),
                    },
                ),
            )
        return updated

    async def get_manifest(self, tenant_id: UUID, asset_id: UUID) -> ManifestResponse:
        """Return the recursive containment tree rooted at `asset_id`."""
        root = await self._assets.get(tenant_id, asset_id)
        if root is None:
            raise AssetNotFoundError(asset_id)
        descendants = await self._assets.get_descendants(tenant_id, asset_id)
        # Build entries by id, then attach each to its parent.
        entries: dict[UUID, ManifestEntry] = {}
        for asset, depth in descendants:
            entries[asset.id] = ManifestEntry(
                asset_id=asset.id,
                name=asset.name,
                parent_asset_id=asset.parent_asset_id,
                depth=depth,
                children=[],
            )
        children_of_root: list[ManifestEntry] = []
        for entry in entries.values():
            if entry.parent_asset_id == asset_id:
                children_of_root.append(entry)
            elif entry.parent_asset_id in entries:
                entries[entry.parent_asset_id].children.append(entry)
        return ManifestResponse(
            asset_id=root.id,
            name=root.name,
            children=children_of_root,
        )

    # -- External (non-RFID) positions (Phase C) --

    async def record_external_position(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        payload: ExternalLocationCreate,
    ) -> ExternalLocationResponse:
        if self._external is None:
            raise RuntimeError("external_location_repo not configured")
        # Verify asset exists in tenant.
        asset = await self._assets.get(tenant_id, asset_id)
        if asset is None:
            raise AssetNotFoundError(asset_id)
        position = await self._external.insert(tenant_id, asset_id, payload)
        external_locations_counter.add(1, {"tenant_id": str(tenant_id), "source": payload.source})
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.external_position_recorded",
            resource_type="asset",
            resource_id=asset_id,
            changes={"source": payload.source},
        )
        if self._event_bus is not None:
            await self._event_bus.publish(
                Topic.EXTERNAL_LOCATION_RECORDED,
                Event(
                    id=uuid_mod.uuid4(),
                    topic=Topic.EXTERNAL_LOCATION_RECORDED,
                    timestamp=position.recorded_at,
                    payload={
                        "tenant_id": str(tenant_id),
                        "asset_id": str(asset_id),
                        "latitude": position.latitude,
                        "longitude": position.longitude,
                        "source": position.source,
                        "recorded_at": position.recorded_at.isoformat(),
                    },
                ),
            )
        return position

    async def list_external_positions(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExternalLocationResponse]:
        if self._external is None:
            raise RuntimeError("external_location_repo not configured")
        return await self._external.list_for_asset(tenant_id, asset_id, limit=limit, offset=offset)

    # -- Floor positions (Sprint 65 — BYO precomputed (x, y)) --

    async def record_floor_position(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        asset_id: UUID,
        payload: FloorPositionCreate,
    ) -> FloorPositionResponse:
        """Persist a precomputed floor ``(x, y)`` fix (``source='precomputed'``).

        Guards: the asset must exist in the tenant (``AssetNotFoundError`` → 404)
        and the ``site_id`` must belong to the tenant (``AssetPositionSiteError``
        → 422). ``tenant_id`` is always stamped server-side, never from the body.
        """
        if self._positions is None or self._sites is None:
            raise RuntimeError("position_repo/site_repo not configured")
        asset = await self._assets.get(tenant_id, asset_id)
        if asset is None:
            raise AssetNotFoundError(asset_id)
        site = await self._sites.get(tenant_id, payload.site_id)
        if site is None:
            raise AssetPositionSiteError(payload.site_id)
        recorded_at = payload.recorded_at or datetime.now(UTC)
        position = await self._positions.insert(
            tenant_id,
            asset_id,
            recorded_at=recorded_at,
            position=payload,
            source="precomputed",
        )
        await self._audit.log(
            tenant_id=tenant_id,
            user_id=user_id,
            action="asset.floor_position_recorded",
            resource_type="asset",
            resource_id=asset_id,
            changes={"source": "precomputed", "site_id": str(payload.site_id)},
        )
        return position

    async def list_floor_path(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[FloorPositionResponse]:
        if self._positions is None:
            raise RuntimeError("position_repo not configured")
        return await self._positions.list_floor_path(
            tenant_id, asset_id, since=since, until=until, source=source, limit=limit
        )

    # -- Location & path (Sprint 15 — view + path API) --

    async def get_current_location(
        self, tenant_id: UUID, asset_id: UUID
    ) -> AssetCurrentLocation | None:
        if self._asset_location is None:
            raise RuntimeError("asset_location_repo not configured")
        return await self._asset_location.get_current_location(tenant_id, asset_id)

    async def list_current_locations(
        self, tenant_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> list[AssetCurrentLocation]:
        if self._asset_location is None:
            raise RuntimeError("asset_location_repo not configured")
        return list(
            await self._asset_location.list_current_locations(tenant_id, limit=limit, offset=offset)
        )

    async def get_asset_path(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int = 1000,
    ) -> list[AssetPathPoint]:
        if self._asset_location is None:
            raise RuntimeError("asset_location_repo not configured")
        return list(
            await self._asset_location.get_asset_path(
                tenant_id, asset_id, since=since, until=until, limit=limit
            )
        )

    async def get_assets_in_zone(
        self,
        tenant_id: UUID,
        zone_id: UUID,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AssetInZoneSummary]:
        if self._asset_location is None:
            raise RuntimeError("asset_location_repo not configured")
        return list(
            await self._asset_location.get_assets_in_zone(
                tenant_id, zone_id, limit=limit, offset=offset
            )
        )

    async def _assert_no_parent_cycle(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        proposed_parent_id: UUID,
    ) -> None:
        """Walk ``proposed_parent_id``'s ancestry; raise if we hit ``asset_id``.

        Bounded by the current containment depth (typically <10 — pallet of
        cases of cartons). A hard cap of 64 hops protects against
        already-corrupt data; we'd rather fail loudly than loop.
        """
        seen: set[UUID] = {asset_id}
        cursor: UUID | None = proposed_parent_id
        for _ in range(64):
            if cursor is None:
                return
            if cursor in seen:
                raise ValueError("load would create a containment cycle")
            seen.add(cursor)
            ancestor = await self._assets.get(tenant_id, cursor)
            if ancestor is None:
                return  # parent vanished mid-flight; let set_parent decide
            cursor = ancestor.parent_asset_id
        raise ValueError("asset containment depth exceeds 64 — refusing to load")
