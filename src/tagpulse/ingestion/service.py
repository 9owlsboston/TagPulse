"""Ingestion service — validates and persists tag read events."""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from tagpulse.api.services.telemetry_service import TelemetryService
from tagpulse.core.config import settings
from tagpulse.core.otel_metrics import (
    events_rejected_clock_counter,
    geofence_candidates_per_evaluation,
    geofence_evaluation_duration,
    geofence_transitions_counter,
    ingestion_counter,
    inventory_unmapped_sgtin_counter,
    stock_item_auto_create_blocked_counter,
    stock_item_auto_created_counter,
    stock_movements_recorded_counter,
    subject_zone_changed_counter,
    tag_reads_without_asset_counter,
)
from tagpulse.core.telemetry_caches import SUBJECT_KINDS_CACHE
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.ingestion.clock import ClockRejectionError, check_clock_window
from tagpulse.ingestion.tag_data import cap_tag_data
from tagpulse.models.schemas import (
    AssetTagBindingResponse,
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
from tagpulse.repositories.timescaledb.tags import TimescaleTagRepository
from tagpulse.repositories.timescaledb.telemetry import (
    TimescaleTelemetryReadingsRepository,
)
from tagpulse.repositories.timescaledb.tenants import TimescaleTenantRepository
from tagpulse.rfid.epc import decode_epc_hex, gtin14_from_decoded
from tagpulse.services.tags import normalize_epc_hex

logger = logging.getLogger(__name__)

# Sprint 58: backfill flag for the current ingest call. Set at the top of
# ``ingest()`` / ``ingest_batch()`` and consumed by ``_event_payload()`` which
# stamps ``backfill=True`` onto every event payload published downstream.
# Subscribers (rule evaluator, read-frequency analytics) early-return on the
# flag so historical reads replayed via ``POST /tag-reads?backfill=true`` go
# through the full ingest pipeline (validation, enrichment, hypertable insert,
# telemetry rollups) but never fire alerts. ContextVar is the right scope: one
# value per async task, no cross-request leakage on a shared service instance.
_BACKFILL_CTX: ContextVar[bool] = ContextVar("tagpulse_ingest_backfill", default=False)


def _event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload`` augmented with ``backfill=True`` when in a backfill ingest.

    Defined at module scope (not on the service) so the dozen Event
    constructions in this file each add a single ``**_event_payload(...)`` call
    instead of threading a ``backfill`` parameter through every helper.
    """
    if _BACKFILL_CTX.get():
        return {**payload, "backfill": True}
    return payload


# Bounded process-local zone caches. Cross requests within a single worker;
# multi-worker durability lands in Sprint 17 alongside the rules engine's
# Redis state per docs/design/assets-and-zones.md §5. Bounded so a long-running
# worker doesn't accumulate one entry per subject ever seen — oldest insertion
# is evicted when the cap is hit (FIFO via dict insertion order).
_ZONE_CACHE_MAX = 10_000

_LAST_ZONE_BY_ASSET: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID | None] = {}
_LAST_ZONE_BY_STOCK_ITEM: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID | None] = {}

# Sprint 17a: separate caches for geofence-zone transitions per subject. They
# never collide with reader-bound caches above because zone_kind is included
# in the emitted event payload, and downstream consumers key on subject_id.
_LAST_GEOFENCE_BY_ASSET: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID | None] = {}
_LAST_GEOFENCE_BY_STOCK_ITEM: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID | None] = {}

# GTIN -> product_id and (tenant, product, scope_kind) -> mappings caches.
# Same boundedness story as the zone caches; sized smaller because catalog
# churn is much rarer than subject churn.
_GTIN_CACHE_MAX = 4_096
_MAPPING_CACHE_MAX = 1_024
_TRACKING_MODES_CACHE_MAX = 1_024

_GTIN_TO_PRODUCT_ID: dict[tuple[uuid.UUID, str], uuid.UUID | None] = {}
_TRACKING_MODES: dict[uuid.UUID, tuple[str, ...]] = {}

# Tag-borne numeric keys that are read **metadata**, not device-sensor metrics,
# so they must NOT be mirrored into ``telemetry_readings`` (they have no model
# entry → they would quarantine as ``unknown_metric`` on every read and pollute
# charts). ``read_count`` is the per-snapshot read count (WM ``cnt``): it stays
# on ``tag_reads.sensor_data`` — the source of truth the weighted asset fusion
# (positioning + future temp/humidity) reads from — but it is not telemetry.
_NON_TELEMETRY_SENSOR_KEYS = frozenset({"read_count"})

# Sprint 19: cache for tenants.telemetry_subject_kinds. Sprint 21
# replaced the unbounded process-local dict with the shared
# ``SUBJECT_KINDS_CACHE`` (30 s TTL) so a ``PATCH /tenant/config``
# flip propagates within one TTL across workers without requiring
# a restart. Local writers also call ``invalidate_subject_kinds``
# for immediate convergence within the writing worker.

# Phase A-C audit mitigations (#5, #6): bound the per-read DB hits in the
# asset-tracking branch. Bindings rarely flip; mobility almost never does.
_BINDING_CACHE_MAX = 8_192
_MOBILITY_CACHE_MAX = 4_096
# (tenant, binding_value) -> (asset_id, binding_id) ; None = "no active binding"
_BINDING_BY_VALUE: dict[tuple[uuid.UUID, str], tuple[uuid.UUID, uuid.UUID] | None] = {}
# (tenant, device_id) -> "fixed" | "mobile"
_DEVICE_MOBILITY: dict[tuple[uuid.UUID, uuid.UUID], str] = {}


def _bounded_set(cache: dict[Any, Any], key: Any, value: Any, maxsize: int) -> None:
    """FIFO-evicting setter; pops the oldest insertion when ``len(cache)`` hits
    ``maxsize``. Idempotent re-inserts (key already present) do not change
    insertion order — callers that need LRU semantics should ``pop`` first.
    """
    if key not in cache and len(cache) >= maxsize:
        # Evict the oldest entry; iter(dict) yields in insertion order.
        try:
            oldest = next(iter(cache))
        except StopIteration:  # pragma: no cover — defensive
            oldest = None
        if oldest is not None:
            cache.pop(oldest, None)
    cache[key] = value


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
        tenant_repo: TimescaleTenantRepository | None = None,
        telemetry_readings_repo: (TimescaleTelemetryReadingsRepository | None) = None,
        tag_repo: TimescaleTagRepository | None = None,
        usage_meter: UsageMeter | None = None,
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
        self._tenant_repo = tenant_repo
        self._telemetry_readings_repo = telemetry_readings_repo
        self._tag_repo = tag_repo
        self._usage_meter = usage_meter

    async def ingest(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
        *,
        backfill: bool = False,
    ) -> TagReadResponse:
        """Validate, persist, and publish a single tag read.

        ``backfill=True`` (Sprint 58, resolved Q1): runs the full ingest path
        but stamps published events with ``backfill=True`` so rule evaluators
        and read-frequency analytics skip them. Use for replaying historical
        reads from the demo-tenant seed bundle without firing alerts that
        the seed step is simultaneously trying to control.
        """
        token = _BACKFILL_CTX.set(backfill)
        try:
            return await self._ingest_impl(tenant_id, read)
        finally:
            _BACKFILL_CTX.reset(token)

    async def _ingest_impl(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadResponse:
        """Inner implementation — see :meth:`ingest`."""
        normalized = self._normalize(tenant_id, read)
        reason = check_clock_window(normalized.timestamp)
        if reason is not None:
            await self._repo.record_rejection(tenant_id, normalized, reason)
            events_rejected_clock_counter.add(
                1,
                {
                    "tenant_id": str(tenant_id),
                    "reason": reason,
                    "mode": "enforce" if settings.ingest_clock_enforce else "observe",
                },
            )
            if self._usage_meter is not None:
                self._usage_meter.record(tenant_id, "events_rejected_clock", "events")
            logger.warning(
                "Tag read out-of-window: device=%s ts=%s reason=%s mode=%s",
                normalized.device_id,
                normalized.timestamp,
                reason,
                "enforce" if settings.ingest_clock_enforce else "observe",
            )
            if settings.ingest_clock_enforce:
                raise ClockRejectionError(reason)
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
            await self._device_repo.record_last_seen(tenant_id, normalized.device_id, now)
            await self._device_repo.record_connection_state(
                tenant_id,
                normalized.device_id,
                "online",
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
                payload=_event_payload(
                    {
                        "tag_read_id": str(result.id),
                        "tenant_id": str(tenant_id),
                        "device_id": str(normalized.device_id),
                        "tag_id": normalized.tag_id,
                        "epc": normalized.identity.epc if normalized.identity else None,
                        "tid": normalized.identity.tid if normalized.identity else None,
                        "signal_strength": normalized.signal_strength,
                    }
                ),
            ),
        )
        return result

    async def ingest_batch(
        self,
        tenant_id: uuid.UUID,
        reads: list[TagReadCreate],
        *,
        backfill: bool = False,
    ) -> tuple[int, int]:
        """Validate, persist, enrich, and publish a batch of tag reads.

        Phase B.3: each read goes through the same asset/zone + inventory
        enrichment as the single-read path so batched HTTP/MQTT clients see
        the same ``subject.zone_changed`` and inventory side-effects.

        Sprint 16: out-of-window events (per docs/design/edge-device-contract.md
        §3.5) are dead-lettered + metered and excluded from the inserted set.
        Returns ``(ingested, rejected)``.

        ``backfill=True`` (Sprint 58, resolved Q1): see :meth:`ingest`.
        """
        token = _BACKFILL_CTX.set(backfill)
        try:
            return await self._ingest_batch_impl(tenant_id, reads)
        finally:
            _BACKFILL_CTX.reset(token)

    async def _ingest_batch_impl(
        self, tenant_id: uuid.UUID, reads: list[TagReadCreate]
    ) -> tuple[int, int]:
        """Inner implementation — see :meth:`ingest_batch`."""
        normalized_all = [self._normalize(tenant_id, r) for r in reads]
        normalized: list[TagReadCreate] = []
        rejected = 0
        for read in normalized_all:
            reason = check_clock_window(read.timestamp)
            if reason is not None:
                await self._repo.record_rejection(tenant_id, read, reason)
                events_rejected_clock_counter.add(
                    1,
                    {
                        "tenant_id": str(tenant_id),
                        "reason": reason,
                        "mode": "enforce" if settings.ingest_clock_enforce else "observe",
                    },
                )
                if self._usage_meter is not None:
                    self._usage_meter.record(tenant_id, "events_rejected_clock", "events")
                rejected += 1
                if settings.ingest_clock_enforce:
                    continue
            normalized.append(read)
        inserted = await self._repo.insert_batch(tenant_id, normalized)
        count = len(inserted)
        logger.info(
            "Batch ingested: %d tag reads (%d rejected by clock window)",
            count,
            rejected,
        )
        if self._device_repo and inserted:
            now = datetime.now(UTC)
            # Touch each unique device once; record_last_seen is cheap but
            # unbounded loops here would amplify hot-batch fan-out.
            seen_devices: set[uuid.UUID] = set()
            for read in normalized:
                if read.device_id in seen_devices:
                    continue
                seen_devices.add(read.device_id)
                await self._device_repo.record_last_seen(tenant_id, read.device_id, now)
                await self._device_repo.record_connection_state(
                    tenant_id,
                    read.device_id,
                    "online",
                )
        for read, row in zip(normalized, inserted, strict=True):
            await self._mirror_tag_borne_sensors(tenant_id, read, row.id)
            await self._enrich_with_asset_zone(tenant_id, read, row.id)
            await self._enrich_with_inventory(tenant_id, read, row.id)
            await self._event_bus.publish(
                Topic.TAG_READ_CREATED,
                Event(
                    id=uuid.uuid4(),
                    topic=Topic.TAG_READ_CREATED,
                    timestamp=datetime.now(UTC),
                    payload=_event_payload(
                        {
                            "tag_read_id": str(row.id),
                            "tenant_id": str(tenant_id),
                            "device_id": str(read.device_id),
                            "tag_id": read.tag_id,
                            "epc": read.identity.epc if read.identity else None,
                            "tid": read.identity.tid if read.identity else None,
                            "signal_strength": read.signal_strength,
                        }
                    ),
                ),
            )
        return count, rejected

    def _normalize(self, tenant_id: uuid.UUID, read: TagReadCreate) -> TagReadCreate:
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
        """Mirror declared numeric tag-borne sensor values into telemetry rows.

        Per [docs/design/rfid-tag-data-model.md §6 / D4]: tag-borne sensor
        readings are written to the telemetry pipeline with provenance
        metadata so analytics treat them uniformly with device-borne
        metrics.

        Source columns (Sprint 53 phase I): numeric values are collected
        from BOTH ``read.sensor_data`` (the structured, typed sensor blob
        populated by wire-format parsers — e.g. v2 t=0/t=1 ``cnt``/``tmp``
        /``hum`` mapped to ``read_count``/``temperature_c``/``humidity_pct``
        in :func:`tagpulse.ingestion.mqtt_subscriber._wm_sensor_data`) and
        ``read.tag_data`` (free-form tag memory inlined by HTTP clients
        and v1 callers). Keys prefixed with ``_`` are skipped. On
        collision, ``tag_data`` overrides ``sensor_data`` (explicit
        client-supplied value wins).

        Sprint 19 fan-out: in addition to the device-scoped
        ``telemetry_readings`` row (subject_kind='device') written via
        ``telemetry_service.ingest_reading``, each numeric key also lands
        as one ``telemetry_readings`` row per resolved subject (asset /
        stock_item / lot) the tenant has opted into via
        ``tenants.telemetry_subject_kinds``. Resolution is best-effort:
        an unresolved subject is logged at INFO level
        (``telemetry.subject_unresolved``) and skipped — never an error.
        """
        provenance: dict[str, Any] = {
            "source": "tag",
            "tag_read_id": str(tag_read_id),
        }
        if read.identity and read.identity.epc:
            provenance["epc"] = read.identity.epc
        if read.identity and read.identity.tid:
            provenance["tid"] = read.identity.tid

        # Collect numerics from sensor_data first, then let tag_data override on key collision.
        merged: dict[str, float] = {}
        for blob in (read.sensor_data, read.tag_data):
            if not blob:
                continue
            for k, v in blob.items():
                if k.startswith("_") or k in _NON_TELEMETRY_SENSOR_KEYS:
                    continue
                if isinstance(v, bool) or not isinstance(v, int | float):
                    continue
                merged[k] = float(v)
        numeric_items = list(merged.items())
        if not numeric_items:
            return

        # 1) legacy device-scoped path (Sprint 14 contract; unchanged).
        if self._telemetry_service is not None:
            for key, value in numeric_items:
                reading = TelemetryReading(
                    timestamp=read.timestamp,
                    metric_name=key,
                    metric_value=value,
                    metadata=provenance,
                )
                try:
                    await self._telemetry_service.ingest_reading(tenant_id, read.device_id, reading)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to mirror tag-borne metric %s for tag_read %s",
                        key,
                        tag_read_id,
                    )

        # 2) Sprint 19 subject fan-out.
        if self._telemetry_readings_repo is None:
            return
        opted_in = await self._get_telemetry_subject_kinds(tenant_id)
        non_device = [k for k in opted_in if k != "device"]
        if not non_device:
            return

        subjects = await self._resolve_telemetry_subjects(tenant_id, read)
        if not subjects:
            logger.info(
                "telemetry.subject_unresolved tenant=%s tag=%s tag_read=%s",
                tenant_id,
                read.tag_id,
                tag_read_id,
            )
            return

        for subject_kind, subject_id in subjects:
            if subject_kind not in non_device:
                continue
            for key, value in numeric_items:
                try:
                    inserted = await self._telemetry_readings_repo.insert(
                        tenant_id=tenant_id,
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        timestamp=read.timestamp,
                        metric_name=key,
                        metric_value=value,
                        device_id=read.device_id,
                        source="tag",
                        metadata=provenance,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to fan-out tag-borne metric %s to subject %s/%s for tag_read %s",
                        key,
                        subject_kind,
                        subject_id,
                        tag_read_id,
                    )
                    continue
                # Sprint 20: notify rules engine. Best-effort — failure to
                # publish must not roll back the persisted row.
                try:
                    await self._event_bus.publish(
                        Topic.TELEMETRY_RECORDED,
                        Event(
                            id=inserted.id,
                            topic=Topic.TELEMETRY_RECORDED,
                            timestamp=inserted.timestamp,
                            payload=_event_payload(
                                {
                                    "tenant_id": str(tenant_id),
                                    "subject_kind": subject_kind,
                                    "subject_id": str(subject_id),
                                    "metric_name": key,
                                    "metric_value": value,
                                    "unit": inserted.unit,
                                    "device_id": str(read.device_id),
                                    "source": "tag",
                                    "timestamp": inserted.timestamp.isoformat(),
                                }
                            ),
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "telemetry.recorded publish failed for %s/%s metric %s",
                        subject_kind,
                        subject_id,
                        key,
                    )

    async def _resolve_telemetry_subjects(
        self,
        tenant_id: uuid.UUID,
        read: TagReadCreate,
    ) -> list[tuple[str, uuid.UUID]]:
        """Resolve a tag-read into the list of subject_kind/id targets.

        Looks up:

        * ``asset`` — via the active ``asset_tag_bindings`` row on the
          read's ``tag_id`` (uses the existing per-process cache).
        * ``stock_item`` — via the active stock_item bound to the
          read's ``tag_id`` (no auto-create here; that is the
          inventory branch's job).
        * ``lot`` — derived from the resolved stock_item's ``lot_id``.

        Returns an empty list if nothing resolves; callers log at INFO.
        """
        subjects: list[tuple[str, uuid.UUID]] = []

        if self._binding_repo is not None and read.tag_id:
            cache_key = (tenant_id, read.tag_id)
            cached = _BINDING_BY_VALUE.get(cache_key)
            if cached is None and cache_key not in _BINDING_BY_VALUE:
                binding = await self._binding_repo.get_active_by_value(tenant_id, read.tag_id)
                value = (binding.asset_id, binding.id) if binding is not None else None
                _bounded_set(
                    _BINDING_BY_VALUE,
                    cache_key,
                    value,
                    _BINDING_CACHE_MAX,
                )
                cached = value
            if cached is not None:
                subjects.append(("asset", cached[0]))

        if self._stock_repo is not None and read.tag_id:
            # Subject fan-out resolution covers both EPC- and TID-bound
            # stock items; the inventory branch may also auto-create on
            # first sight via a different binding_kind. We try the most
            # common kind ('epc') and fall back to ('tid'); a future
            # change can plumb the resolved binding_kind through here.
            stock = await self._stock_repo.get_active_by_binding(tenant_id, "epc", read.tag_id)
            if stock is None:
                stock = await self._stock_repo.get_active_by_binding(tenant_id, "tid", read.tag_id)
            if stock is not None:
                subjects.append(("stock_item", stock.id))
                if stock.lot_id is not None:
                    subjects.append(("lot", stock.lot_id))

        return subjects

    async def _get_telemetry_subject_kinds(self, tenant_id: uuid.UUID) -> tuple[str, ...]:
        """Cached read of ``tenants.telemetry_subject_kinds`` (Sprint 21
        TTL cache; see :mod:`tagpulse.core.telemetry_caches`)."""
        cached = SUBJECT_KINDS_CACHE.get(tenant_id)
        if cached is not None:
            return cached
        if self._tenant_repo is None:
            value: tuple[str, ...] = ("device",)
        else:
            kinds = await self._tenant_repo.get_telemetry_subject_kinds(tenant_id)
            value = tuple(kinds)
        SUBJECT_KINDS_CACHE.set(tenant_id, value)
        return value

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
            binding_cache_key = (tenant_id, value)
            if binding_cache_key in _BINDING_BY_VALUE:
                cached = _BINDING_BY_VALUE[binding_cache_key]
                if cached is None:
                    continue  # known miss — skip the DB roundtrip
                asset_id, _binding_id = cached
                # Synthesize a thin record; only ``asset_id`` is read below.
                binding = AssetTagBindingResponse.model_construct(
                    id=_binding_id,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    binding_value=value,
                    binding_kind="epc",
                    bound_at=datetime.now(UTC),
                    unbound_at=None,
                    metadata=None,
                )
                break
            binding = await self._binding_repo.get_active_by_value(tenant_id, value)
            if binding is not None:
                _bounded_set(
                    _BINDING_BY_VALUE,
                    binding_cache_key,
                    (binding.asset_id, binding.id),
                    _BINDING_CACHE_MAX,
                )
                break
            _bounded_set(_BINDING_BY_VALUE, binding_cache_key, None, _BINDING_CACHE_MAX)

        if binding is None:
            tag_reads_without_asset_counter.add(1, {"tenant_id": str(tenant_id)})
            return

        # Mobile readers: no fixed-zone lookup; emit no zone transition.
        device_mobility = await self._lookup_device_mobility(tenant_id, read.device_id)
        if device_mobility != "fixed" or self._zone_repo is None:
            # Mobile readers still get a geofence eval if the read carries a fix.
            await self._eval_geofence_for_subject(
                tenant_id=tenant_id,
                subject_kind="asset",
                subject_id=binding.asset_id,
                read=read,
                tag_read_id=tag_read_id,
            )
            return

        zone = await self._zone_repo.get_zone_for_reader(tenant_id, read.device_id)
        new_zone_id = zone.id if zone else None
        cache_key = (tenant_id, binding.asset_id)
        prev_zone_id = _LAST_ZONE_BY_ASSET.get(cache_key)

        # Geofence point-in-polygon evaluation is independent of reader-bound
        # zones — it runs whenever the read carries a GPS fix, even for fixed
        # readers with no reader-bound zone attached. Run it FIRST so the
        # early returns below (no reader-bound transition) don't skip it.
        await self._eval_geofence_for_subject(
            tenant_id=tenant_id,
            subject_kind="asset",
            subject_id=binding.asset_id,
            read=read,
            tag_read_id=tag_read_id,
        )

        if cache_key not in _LAST_ZONE_BY_ASSET:
            # First time we see this asset — seed the cache without firing.
            _bounded_set(_LAST_ZONE_BY_ASSET, cache_key, new_zone_id, _ZONE_CACHE_MAX)
            return

        if new_zone_id == prev_zone_id:
            return

        _bounded_set(_LAST_ZONE_BY_ASSET, cache_key, new_zone_id, _ZONE_CACHE_MAX)
        await self._event_bus.publish(
            Topic.SUBJECT_ZONE_CHANGED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.SUBJECT_ZONE_CHANGED,
                timestamp=datetime.now(UTC),
                payload=_event_payload(
                    {
                        "tenant_id": str(tenant_id),
                        "subject_kind": "asset",
                        "subject_id": str(binding.asset_id),
                        "zone_kind": "reader_bound",
                        "from_zone_id": str(prev_zone_id) if prev_zone_id else None,
                        "to_zone_id": str(new_zone_id) if new_zone_id else None,
                        "device_id": str(read.device_id),
                        "tag_id": read.tag_id,
                        "epc": read.identity.epc if read.identity else None,
                        "tid": read.identity.tid if read.identity else None,
                        "tag_read_id": str(tag_read_id),
                        "timestamp": read.timestamp.isoformat(),
                    }
                ),
            ),
        )
        subject_zone_changed_counter.add(1, {"tenant_id": str(tenant_id), "subject_kind": "asset"})

    async def _eval_geofence_for_subject(
        self,
        *,
        tenant_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        read: TagReadCreate,
        tag_read_id: uuid.UUID,
    ) -> None:
        """Geofence point-in-polygon evaluation per
        [docs/design/geofencing-and-map.md §4](../../../docs/design/geofencing-and-map.md).

        Only fires when:

        - ``settings.geofence_evaluation_enabled`` is true (rollout flag).
        - the read carries a ``location`` with lat/lon.
        - the subject's last-seen geofence-zone differs from the new one.

        Emits ``subject.zone_changed`` with ``zone_kind='geofence'`` so
        downstream rule evaluators can disambiguate from reader-bound
        transitions.
        """
        if not settings.geofence_evaluation_enabled:
            return
        if self._zone_repo is None or read.location is None:
            return
        lat = read.location.latitude
        lon = read.location.longitude

        # Lazy import to keep the hot-path module load minimal in deployments
        # that disable geofencing entirely.
        import time

        from tagpulse.geo import point_in_polygon

        start = time.perf_counter()
        candidates = await self._zone_repo.find_geofence_candidates(tenant_id, lat, lon)
        geofence_candidates_per_evaluation.record(len(candidates), {"tenant_id": str(tenant_id)})

        new_zone_id: uuid.UUID | None = None
        for candidate in candidates:
            polygon = candidate.polygon_geojson
            if not polygon:
                continue
            try:
                ring_raw = polygon["coordinates"][0]
                ring = [(float(p[0]), float(p[1])) for p in ring_raw]
            except (KeyError, IndexError, TypeError, ValueError):
                logger.warning(
                    "geofence.malformed_polygon",
                    extra={
                        "tenant_id": str(tenant_id),
                        "zone_id": str(candidate.id),
                    },
                )
                continue
            if point_in_polygon(lat, lon, ring):
                new_zone_id = candidate.id
                break  # zones are ordered by created_at; first match wins

        geofence_evaluation_duration.record(
            time.perf_counter() - start, {"tenant_id": str(tenant_id)}
        )

        cache = _LAST_GEOFENCE_BY_ASSET if subject_kind == "asset" else _LAST_GEOFENCE_BY_STOCK_ITEM
        cache_key = (tenant_id, subject_id)
        prev_zone_id = cache.get(cache_key)
        seen_before = cache_key in cache
        _bounded_set(cache, cache_key, new_zone_id, _ZONE_CACHE_MAX)
        if not seen_before:
            return  # seed; first fix never fires
        if new_zone_id == prev_zone_id:
            return

        await self._event_bus.publish(
            Topic.SUBJECT_ZONE_CHANGED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.SUBJECT_ZONE_CHANGED,
                timestamp=datetime.now(UTC),
                payload=_event_payload(
                    {
                        "tenant_id": str(tenant_id),
                        "subject_kind": subject_kind,
                        "subject_id": str(subject_id),
                        "zone_kind": "geofence",
                        "from_zone_id": str(prev_zone_id) if prev_zone_id else None,
                        "to_zone_id": str(new_zone_id) if new_zone_id else None,
                        "device_id": str(read.device_id),
                        "tag_id": read.tag_id,
                        "epc": read.identity.epc if read.identity else None,
                        "tid": read.identity.tid if read.identity else None,
                        "tag_read_id": str(tag_read_id),
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": read.timestamp.isoformat(),
                    }
                ),
            ),
        )
        geofence_transitions_counter.add(
            1, {"tenant_id": str(tenant_id), "subject_kind": subject_kind}
        )
        subject_zone_changed_counter.add(
            1,
            {
                "tenant_id": str(tenant_id),
                "subject_kind": subject_kind,
                "zone_kind": "geofence",
            },
        )

    async def _lookup_device_mobility(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> str:
        """Return device mobility ('fixed' | 'mobile'), defaulting to 'fixed'.

        Cached in-process; mobility is changed by an admin via /devices PATCH
        and a stale value at worst delays the switch by one worker restart.
        """
        cache_key = (tenant_id, device_id)
        cached = _DEVICE_MOBILITY.get(cache_key)
        if cached is not None:
            return cached
        if self._device_repo is None:
            return "fixed"
        device = await self._device_repo.get(tenant_id, device_id)
        mobility = getattr(device, "mobility", "fixed") if device is not None else "fixed"
        _bounded_set(_DEVICE_MOBILITY, cache_key, mobility, _MOBILITY_CACHE_MAX)
        return mobility

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

        # Tenant must have inventory mode enabled. Cached in-process; tenants
        # rarely flip modes, and a stale read costs at most one un-emitted
        # transition per worker per restart.
        if not await self._tenant_has_inventory_mode(tenant_id):
            return

        product_id = await self._lookup_product_id_by_gtin(tenant_id, gtin)
        if product_id is None:
            inventory_unmapped_sgtin_counter.add(1, {"tenant_id": str(tenant_id)})
            return

        epc = read.identity.epc
        stock_item = await self._stock_repo.get_active_by_binding(tenant_id, "epc", epc)
        if stock_item is None:
            # Sprint 50 Phase D3 (ADR 028 §"Soft-asset interaction"):
            # The tag registry is the authoritative answer to "does this
            # tenant own this EPC?". Auto-create only when the EPC has a
            # row in ``tags`` with a non-terminal status. Reading ``tags``
            # is acceptable here because this branch is on the rare path
            # (first-ever read for an EPC) — the steady-state inventory
            # hot path skips this block via the ``stock_items`` lookup
            # above. The MQTT ingest write path (``self._repo.create``)
            # still never touches ``tags``.
            if self._tag_repo is not None:
                tag = await self._tag_repo.get_by_epc(tenant_id, normalize_epc_hex(epc))
                if tag is None or tag.status not in {"registered", "active"}:
                    stock_item_auto_create_blocked_counter.add(1, {"tenant_id": str(tenant_id)})
                    return
            lot_id = await self._infer_lot_id(tenant_id, product_id, read.tag_data)
            try:
                stock_item = await self._stock_repo.create(
                    tenant_id,
                    StockItemCreate(
                        product_id=product_id,
                        lot_id=lot_id,
                        binding_value=epc,
                        binding_kind="epc",
                    ),
                )
            except ValueError:
                # Race: another worker just created the same active binding.
                stock_item = await self._stock_repo.get_active_by_binding(tenant_id, "epc", epc)
                if stock_item is None:
                    return
            else:
                stock_item_auto_created_counter.add(1, {"tenant_id": str(tenant_id)})

        # Mobile readers (or no zone repo) skip zone resolution — no
        # transition can be inferred without a fixed reader-bound zone.
        device_mobility = await self._lookup_device_mobility(tenant_id, read.device_id)
        if device_mobility != "fixed" or self._zone_repo is None:
            await self._eval_geofence_for_subject(
                tenant_id=tenant_id,
                subject_kind="stock_item",
                subject_id=stock_item.id,
                read=read,
                tag_read_id=tag_read_id,
            )
            return

        zone = await self._zone_repo.get_zone_for_reader(tenant_id, read.device_id)
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
        _bounded_set(_LAST_ZONE_BY_STOCK_ITEM, cache_key, new_zone_id, _ZONE_CACHE_MAX)
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
            stock_movements_recorded_counter.add(1, {"tenant_id": str(tenant_id)})
            if self._usage_meter is not None:
                self._usage_meter.record(tenant_id, "inventory_movements", "events")

        await self._event_bus.publish(
            Topic.SUBJECT_ZONE_CHANGED,
            Event(
                id=uuid.uuid4(),
                topic=Topic.SUBJECT_ZONE_CHANGED,
                timestamp=datetime.now(UTC),
                payload=_event_payload(
                    {
                        "tenant_id": str(tenant_id),
                        "subject_kind": "stock_item",
                        "subject_id": str(stock_item.id),
                        "zone_kind": "reader_bound",
                        "product_id": str(product_id),
                        "from_zone_id": str(prev_zone_id) if prev_zone_id else None,
                        "to_zone_id": str(new_zone_id) if new_zone_id else None,
                        "device_id": str(read.device_id),
                        "epc": epc,
                        "tag_read_id": str(tag_read_id),
                        "timestamp": read.timestamp.isoformat(),
                    }
                ),
            ),
        )
        subject_zone_changed_counter.add(
            1, {"tenant_id": str(tenant_id), "subject_kind": "stock_item"}
        )
        await self._eval_geofence_for_subject(
            tenant_id=tenant_id,
            subject_kind="stock_item",
            subject_id=stock_item.id,
            read=read,
            tag_read_id=tag_read_id,
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
        if not tag_data or self._tag_data_mapping_repo is None or self._lot_repo is None:
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
                lots = await self._lot_repo.list_for_product(tenant_id, product_id, limit=1000)
                for lot in lots:
                    if lot.lot_code == value:
                        return lot.id
                return None
        return None

    async def _tenant_has_inventory_mode(self, tenant_id: uuid.UUID) -> bool:
        """Return True iff the tenant opted into inventory tracking.

        Cached in-process to keep the SGTIN hot-path off the database. The
        cache is bounded; tenants flipping modes will see the new value once
        the worker restarts (acceptable per docs/design/tracking-modes.md \u00a76).
        """
        cached = _TRACKING_MODES.get(tenant_id)
        if cached is not None:
            return "inventory" in cached
        if self._tenant_repo is None:
            # Backward-compat: ingestion services constructed without a tenant
            # repo (older tests) treat every tenant as inventory-enabled so
            # behavior matches the pre-guard implementation.
            return True
        modes = await self._tenant_repo.get_tracking_modes(tenant_id)
        _bounded_set(_TRACKING_MODES, tenant_id, tuple(modes), _TRACKING_MODES_CACHE_MAX)
        return "inventory" in modes

    async def _lookup_product_id_by_gtin(self, tenant_id: uuid.UUID, gtin: str) -> uuid.UUID | None:
        """Resolve ``(tenant, gtin) -> product_id`` with a bounded LRU cache.

        Both hits and misses are cached; a missing product is far more common
        than a hit when bulk-importing, and re-querying the DB on every read
        of an unmapped SGTIN would dominate the budget.
        """
        assert self._product_repo is not None  # noqa: S101 - guarded by caller
        key = (tenant_id, gtin)
        if key in _GTIN_TO_PRODUCT_ID:
            return _GTIN_TO_PRODUCT_ID[key]
        product = await self._product_repo.get_by_gtin(tenant_id, gtin)
        product_id = product.id if product is not None else None
        _bounded_set(_GTIN_TO_PRODUCT_ID, key, product_id, _GTIN_CACHE_MAX)
        return product_id
