"""Ingestion service — validates and persists tag read events."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.otel_metrics import (
    ingestion_counter,
    inventory_unmapped_sgtin_counter,
    stock_item_auto_created_counter,
    stock_movements_recorded_counter,
    subject_zone_changed_counter,
    tag_reads_without_asset_counter,
)
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.ingestion.tag_data import cap_tag_data
from tagpulse.models.schemas import (
    Identity,
    StockItemCreate,
    TagReadCreate,
    TagReadResponse,
    TelemetryReading,
)
from tagpulse.repositories.protocols import DeviceRepository, TagReadRepository
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
from tagpulse.rfid.epc import decode_epc_hex, gtin14_from_decoded

logger = logging.getLogger(__name__)

# Process-local last-zone-by-asset cache. Crosses requests within a single
# worker; multi-worker durability lands in Sprint 17 alongside the rules
# engine's Redis state per docs/design/assets-and-zones.md §5.
_LAST_ZONE_BY_ASSET: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID | None] = {}

# Mirrors the asset cache for stock_item zone transitions (Sprint 15b Phase D.5).
_LAST_ZONE_BY_STOCK_ITEM: dict[
    tuple[uuid.UUID, uuid.UUID], uuid.UUID | None
] = {}


class IngestionService:
    """Accepts tag reads, persists them, and publishes internal events."""

    def __init__(
        self,
        repo: TagReadRepository,
        event_bus: EventBus,
        device_repo: DeviceRepository | None = None,
        telemetry_service: TelemetryService | None = None,
        binding_repo: TimescaleAssetTagBindingRepository | None = None,
        zone_repo: TimescaleZoneRepository | None = None,
        product_repo: TimescaleProductRepository | None = None,
        lot_repo: TimescaleLotRepository | None = None,
        stock_repo: TimescaleStockItemRepository | None = None,
        movement_repo: TimescaleStockMovementRepository | None = None,
        tag_data_mapping_repo: TimescaleTagDataMappingRepository | None = None,
    ) -> None:
        self._repo = repo
        self._event_bus = event_bus
        self._device_repo = device_repo
        self._telemetry_service = telemetry_service
        self._binding_repo = binding_repo
        self._zone_repo = zone_repo
        self._product_repo = product_repo
        self._lot_repo = lot_repo
        self._stock_repo = stock_repo
        self._movement_repo = movement_repo
        self._tag_data_mapping_repo = tag_data_mapping_repo

    async def ingest(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadResponse:
        """Validate, persist, and publish a single tag read."""
        normalized = self._normalize(tenant_id, read)
        result = await self._repo.insert(tenant_id, normalized)
        ingestion_counter.add(1, {"tenant_id": str(tenant_id), "protocol": "http"})
        logger.info(
            "Tag read ingested: device=%s tag=%s ts=%s",
            normalized.device_id,
            normalized.tag_id,
            normalized.timestamp,
        )
        if self._device_repo:
            now = datetime.now(UTC)
            await self._device_repo.record_last_seen(
                tenant_id, normalized.device_id, now
            )
            await self._device_repo.record_connection_state(
                tenant_id, normalized.device_id, "online",
            )
        await self._mirror_tag_borne_sensors(tenant_id, normalized, result.id)
        await self._enrich_with_asset_zone(tenant_id, normalized, result.id)
        await self._enrich_with_inventory(tenant_id, normalized, result.id)
        await self._event_bus.publish(
            Topic.TAG_READ_CREATED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.TAG_READ_CREATED,
                timestamp=datetime.now(UTC),
                payload={
                    "tag_read_id": str(result.id),
                    "tenant_id": str(tenant_id),
                    "device_id": str(normalized.device_id),
                    "tag_id": normalized.tag_id,
                    "epc": normalized.identity.epc if normalized.identity else None,
                    "tid": normalized.identity.tid if normalized.identity else None,
                    "signal_strength": normalized.signal_strength,
                },
            ),
        )
        return result

    async def ingest_batch(self, tenant_id: uuid.UUID, reads: list[TagReadCreate]) -> int:
        """Validate, persist, and publish a batch of tag reads."""
        normalized = [self._normalize(tenant_id, r) for r in reads]
        count = await self._repo.insert_batch(tenant_id, normalized)
        logger.info("Batch ingested: %d tag reads", count)
        for read in normalized:
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

    def _normalize(
        self, tenant_id: uuid.UUID, read: TagReadCreate
    ) -> TagReadCreate:
        """Apply EPC decode, tag_id defaulting, and tag_data inline cap."""
        identity = read.identity
        if identity and identity.epc_hex and not identity.epc:
            scheme, decoded = decode_epc_hex(identity.epc_hex)
            uri = decoded.get("uri") if isinstance(decoded, dict) else None
            identity = Identity(
                epc=uri or identity.epc_hex,
                epc_hex=identity.epc_hex,
                epc_scheme=scheme,
                epc_decoded=decoded or None,
                tid=identity.tid,
                user_memory_hex=identity.user_memory_hex,
            )

        # Determine effective tag_id
        effective_tag_id = read.tag_id
        if not effective_tag_id and identity:
            effective_tag_id = identity.epc or identity.tid or identity.epc_hex
        if not effective_tag_id:
            effective_tag_id = ""

        capped = cap_tag_data(read.tag_data, tenant_id=str(tenant_id))

        return TagReadCreate(
            device_id=read.device_id,
            tag_id=effective_tag_id,
            timestamp=read.timestamp,
            signal_strength=read.signal_strength,
            sensor_data=read.sensor_data,
            location=read.location,
            identity=identity,
            tag_data=capped,
            reader_antenna=read.reader_antenna,
        )

    async def _mirror_tag_borne_sensors(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        tag_read_id: uuid.UUID,
    ) -> None:
        """Mirror declared numeric tag_data keys into device_telemetry rows.

        Per [docs/design/rfid-tag-data-model.md §6 / D4]: tag-borne sensor
        readings are written to ``device_telemetry`` with provenance metadata
        so analytics treat them uniformly with device-borne metrics.
        """
        if not read.tag_data or self._telemetry_service is None:
            return
        provenance: dict[str, Any] = {
            "source": "tag",
            "tag_read_id": str(tag_read_id),
        }
        if read.identity and read.identity.epc:
            provenance["epc"] = read.identity.epc
        if read.identity and read.identity.tid:
            provenance["tid"] = read.identity.tid

        for key, value in read.tag_data.items():
            if key.startswith("_") or not isinstance(value, int | float):
                continue
            reading = TelemetryReading(
                timestamp=read.timestamp,
                metric_name=key,
                metric_value=float(value),
                metadata=provenance,
            )
            try:
                await self._telemetry_service.ingest_reading(
                    tenant_id, read.device_id, reading
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to mirror tag-borne metric %s for tag_read %s",
                    key,
                    tag_read_id,
                )

    async def _enrich_with_asset_zone(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        tag_read_id: uuid.UUID,
    ) -> None:
        """Resolve active asset binding + reader zone, emit subject.zone_changed.

        Per docs/design/assets-and-zones.md §5 and
        docs/design/mobile-carriers-and-manifests.md §4.1: fixed readers
        resolve a reader-bound zone for the device; mobile readers skip the
        zone lookup (their location comes from device telemetry instead).
        Reads with no active asset binding are not an error — count them so
        operators can spot un-registered tags.
        """
        if self._binding_repo is None:
            return

        # Try the most-specific identifier first: EPC, then TID, then tag_id fallback.
        candidates: list[str] = []
        if read.identity:
            if read.identity.epc:
                candidates.append(read.identity.epc)
            if read.identity.tid:
                candidates.append(read.identity.tid)
        if read.tag_id and read.tag_id not in candidates:
            candidates.append(read.tag_id)

        binding = None
        for value in candidates:
            binding = await self._binding_repo.get_active_by_value(tenant_id, value)
            if binding is not None:
                break

        if binding is None:
            tag_reads_without_asset_counter.add(
                1, {"tenant_id": str(tenant_id)}
            )
            return

        # Mobile readers: no fixed-zone lookup; emit no zone transition.
        device_mobility = await self._lookup_device_mobility(
            tenant_id, read.device_id
        )
        if device_mobility != "fixed" or self._zone_repo is None:
            return

        zone = await self._zone_repo.get_zone_for_reader(
            tenant_id, read.device_id
        )
        new_zone_id = zone.id if zone else None
        cache_key = (tenant_id, binding.asset_id)
        prev_zone_id = _LAST_ZONE_BY_ASSET.get(cache_key)

        if cache_key not in _LAST_ZONE_BY_ASSET:
            # First time we see this asset — seed the cache without firing.
            _LAST_ZONE_BY_ASSET[cache_key] = new_zone_id
            return

        if new_zone_id == prev_zone_id:
            return

        _LAST_ZONE_BY_ASSET[cache_key] = new_zone_id
        await self._event_bus.publish(
            Topic.SUBJECT_ZONE_CHANGED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.SUBJECT_ZONE_CHANGED,
                timestamp=datetime.now(UTC),
                payload={
                    "tenant_id": str(tenant_id),
                    "subject_kind": "asset",
                    "subject_id": str(binding.asset_id),
                    "from_zone_id": str(prev_zone_id) if prev_zone_id else None,
                    "to_zone_id": str(new_zone_id) if new_zone_id else None,
                    "device_id": str(read.device_id),
                    "tag_id": read.tag_id,
                    "epc": read.identity.epc if read.identity else None,
                    "tid": read.identity.tid if read.identity else None,
                    "tag_read_id": str(tag_read_id),
                    "timestamp": read.timestamp.isoformat(),
                },
            ),
        )
        subject_zone_changed_counter.add(
            1, {"tenant_id": str(tenant_id), "subject_kind": "asset"}
        )

    async def _lookup_device_mobility(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> str:
        """Return device mobility ('fixed' | 'mobile'), defaulting to 'fixed'."""
        if self._device_repo is None:
            return "fixed"
        device = await self._device_repo.get(tenant_id, device_id)
        if device is None:
            return "fixed"
        return getattr(device, "mobility", "fixed")

    async def _enrich_with_inventory(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        tag_read_id: uuid.UUID,
    ) -> None:
        """Inventory branch: SGTIN -> product -> stock_item -> movement.

        Per docs/design/tracking-modes.md §4.4: SGTIN reads are matched to a
        registered ``products`` row by GTIN-14. A missing product is not an
        error — it is counted so operators can spot un-registered SKUs. When a
        product matches:

        * Look up the active stock_item bound to this EPC; auto-create one if
          absent (lot inferred from ``tag_data`` via ``tag_data_mappings``,
          most-specific scope wins: product > tenant).
        * Resolve the reader-bound zone (skipped for mobile readers).
        * Update the stock_item's ``current_zone_id`` + ``last_seen_at``.
        * On a zone transition: append a ``stock_movements`` row and emit
          ``Topic.SUBJECT_ZONE_CHANGED`` with ``subject_kind='stock_item'``.
        """
        if (
            self._product_repo is None
            or self._stock_repo is None
            or read.identity is None
            or read.identity.epc is None
        ):
            return

        gtin = gtin14_from_decoded(read.identity.epc_decoded)
        if gtin is None:
            return  # not an SGTIN — no product mapping possible

        product = await self._product_repo.get_by_gtin(tenant_id, gtin)
        if product is None:
            inventory_unmapped_sgtin_counter.add(
                1, {"tenant_id": str(tenant_id)}
            )
            return

        epc = read.identity.epc
        stock_item = await self._stock_repo.get_active_by_binding(
            tenant_id, "epc", epc
        )
        if stock_item is None:
            lot_id = await self._infer_lot_id(
                tenant_id, product.id, read.tag_data
            )
            try:
                stock_item = await self._stock_repo.create(
                    tenant_id,
                    StockItemCreate(
                        product_id=product.id,
                        lot_id=lot_id,
                        binding_value=epc,
                        binding_kind="epc",
                    ),
                )
            except ValueError:
                # Race: another worker just created the same active binding.
                stock_item = await self._stock_repo.get_active_by_binding(
                    tenant_id, "epc", epc
                )
                if stock_item is None:
                    return
            else:
                stock_item_auto_created_counter.add(
                    1, {"tenant_id": str(tenant_id)}
                )

        # Mobile readers (or no zone repo) skip zone resolution — no
        # transition can be inferred without a fixed reader-bound zone.
        device_mobility = await self._lookup_device_mobility(
            tenant_id, read.device_id
        )
        if device_mobility != "fixed" or self._zone_repo is None:
            return

        zone = await self._zone_repo.get_zone_for_reader(
            tenant_id, read.device_id
        )
        new_zone_id = zone.id if zone else None

        observation = await self._stock_repo.record_observation(
            tenant_id,
            stock_item.id,
            zone_id=new_zone_id,
            observed_at=read.timestamp,
        )
        if observation is None:
            return
        prev_zone_id, _ = observation

        cache_key = (tenant_id, stock_item.id)
        seen_before = cache_key in _LAST_ZONE_BY_STOCK_ITEM
        _LAST_ZONE_BY_STOCK_ITEM[cache_key] = new_zone_id
        if not seen_before:
            return  # seed; first observation never fires a transition
        if new_zone_id == prev_zone_id:
            return

        if self._movement_repo is not None:
            await self._movement_repo.insert(
                tenant_id,
                stock_item.id,
                from_zone_id=prev_zone_id,
                to_zone_id=new_zone_id,
                movement_type="transfer" if prev_zone_id else "enter",
                device_id=read.device_id,
                occurred_at=read.timestamp,
            )
            stock_movements_recorded_counter.add(
                1, {"tenant_id": str(tenant_id)}
            )

        await self._event_bus.publish(
            Topic.SUBJECT_ZONE_CHANGED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.SUBJECT_ZONE_CHANGED,
                timestamp=datetime.now(UTC),
                payload={
                    "tenant_id": str(tenant_id),
                    "subject_kind": "stock_item",
                    "subject_id": str(stock_item.id),
                    "product_id": str(product.id),
                    "from_zone_id": str(prev_zone_id) if prev_zone_id else None,
                    "to_zone_id": str(new_zone_id) if new_zone_id else None,
                    "device_id": str(read.device_id),
                    "epc": epc,
                    "tag_read_id": str(tag_read_id),
                    "timestamp": read.timestamp.isoformat(),
                },
            ),
        )
        subject_zone_changed_counter.add(
            1, {"tenant_id": str(tenant_id), "subject_kind": "stock_item"}
        )

    async def _infer_lot_id(
        self,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        tag_data: dict[str, Any] | None,
    ) -> uuid.UUID | None:
        """Resolve ``tag_data`` -> ``lot_id`` via tag_data_mappings.

        Most-specific scope wins (product > tenant). Returns ``None`` when no
        mapping declares ``semantic_field='lot'`` or when the referenced
        ``lot_code`` is unknown for the product.
        """
        if (
            not tag_data
            or self._tag_data_mapping_repo is None
            or self._lot_repo is None
        ):
            return None

        # Pull both scopes in priority order; first hit wins.
        scopes: list[tuple[str, uuid.UUID | None]] = [
            ("product", product_id),
            ("tenant", None),
        ]
        for scope_kind, scope_id in scopes:
            mappings = await self._tag_data_mapping_repo.list(
                tenant_id, scope_kind=scope_kind, scope_id=scope_id
            )
            for mapping in mappings:
                if mapping.semantic_field != "lot":
                    continue
                value = tag_data.get(mapping.tag_data_key)
                if not isinstance(value, str) or not value:
                    continue
                lots = await self._lot_repo.list_for_product(
                    tenant_id, product_id, limit=1000
                )
                for lot in lots:
                    if lot.lot_code == value:
                        return lot.id
                return None
        return None
