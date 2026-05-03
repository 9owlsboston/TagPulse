"""Pydantic schemas for tag read messages, devices, and API responses."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

LocationSource = Literal["gps", "fixed", "inferred"]


# -- Sub-models (Sprint 14) --


class Location(BaseModel):
    """Optional location attached to a tag read or sent on the location topic."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_m: float | None = Field(default=None, ge=0.0)
    source: LocationSource = "gps"


class Identity(BaseModel):
    """Optional RFID identity payload (EPC / TID / user memory)."""

    epc: str | None = Field(default=None, max_length=256)
    epc_hex: str | None = Field(default=None, max_length=128)
    epc_scheme: str | None = Field(default=None, max_length=32)
    epc_decoded: dict[str, Any] | None = None
    tid: str | None = Field(default=None, max_length=64)
    user_memory_hex: str | None = None


# -- Tag Reads --


class TagReadCreate(BaseModel):
    """Incoming tag read event — used by both HTTP and MQTT ingestion paths."""

    device_id: UUID
    tag_id: str | None = Field(default=None, max_length=256)
    timestamp: datetime
    signal_strength: float | None = None
    sensor_data: dict[str, Any] | None = None
    # -- Sprint 14: structured optional sub-models --
    location: Location | None = None
    identity: Identity | None = None
    tag_data: dict[str, Any] | None = None
    reader_antenna: int | None = Field(default=None, ge=0, le=255)


class TagReadResponse(BaseModel):
    """Tag read event returned from the API."""

    id: UUID
    device_id: UUID
    tag_id: str
    timestamp: datetime
    signal_strength: float | None
    sensor_data: dict[str, Any] | None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy_m: float | None = None
    location_source: str | None = None
    epc: str | None = None
    epc_hex: str | None = None
    epc_scheme: str | None = None
    epc_decoded: dict[str, Any] | None = None
    tid: str | None = None
    user_memory_hex: str | None = None
    tag_data: dict[str, Any] | None = None
    reader_antenna: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -- Devices --


class DeviceCreate(BaseModel):
    """Register a new device (reader)."""

    name: str = Field(min_length=1, max_length=255)
    device_type: str = Field(default="rfid_reader", max_length=50)
    metadata: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None
    firmware_version: str | None = Field(default=None, max_length=50)


class DeviceUpdate(BaseModel):
    """Partial update for an existing device — all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    device_type: str | None = Field(default=None, max_length=50)
    status: str | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None
    firmware_version: str | None = Field(default=None, max_length=50)


class DeviceResponse(BaseModel):
    """Device returned from the API."""

    id: UUID
    name: str
    device_type: str
    status: str
    metadata: dict[str, Any] | None
    configuration: dict[str, Any] | None
    firmware_version: str | None
    connection_state: str
    last_seen: datetime | None
    mobility: str = "fixed"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeviceStatusUpdate(BaseModel):
    """MQTT status message from a device."""

    connection_state: str = Field(max_length=50)
    firmware_version: str | None = Field(default=None, max_length=50)


# -- Telemetry Model Definitions --


class MetricDefinition(BaseModel):
    """A single metric that a device type can report."""

    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(max_length=50)
    min_value: float | None = None
    max_value: float | None = None
    description: str | None = Field(default=None, max_length=500)


class TelemetryModelCreate(BaseModel):
    """Define the telemetry schema for a device type."""

    device_type: str = Field(min_length=1, max_length=50)
    metrics: list[MetricDefinition] = Field(min_length=1)


class TelemetryModelResponse(BaseModel):
    """Telemetry model definition returned from the API."""

    id: UUID
    device_type: str
    metrics: list[MetricDefinition]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# -- Query & Aggregations --


class ReadsPerHour(BaseModel):
    """Aggregation: tag read count per hour bucket."""

    bucket: datetime
    device_id: UUID
    read_count: int


class UniqueTagsPerWindow(BaseModel):
    """Aggregation: unique tag count per time window."""

    bucket: datetime
    device_id: UUID | None
    unique_tags: int


class DeviceHealthSummary(BaseModel):
    """Device health snapshot."""

    device_id: UUID
    name: str
    status: str
    connection_state: str
    last_seen: datetime | None
    reads_last_hour: int
    error_rate: float


# -- Telemetry / Location / Events (Sprint 14) --


class TelemetryReading(BaseModel):
    """A single telemetry reading inside a batched payload."""

    timestamp: datetime
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: float
    unit: str | None = Field(default=None, max_length=20)
    metadata: dict[str, Any] | None = None


class TelemetryBatch(BaseModel):
    """Batched telemetry payload — HTTP and MQTT share this shape."""

    device_id: UUID
    readings: list[TelemetryReading] = Field(min_length=1)


class TelemetrySingle(BaseModel):
    """Single-reading payload — used by MQTT location/telemetry topics."""

    device_id: UUID
    timestamp: datetime
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: float
    unit: str | None = Field(default=None, max_length=20)
    metadata: dict[str, Any] | None = None


class TelemetryResponse(BaseModel):
    """Persisted telemetry row."""

    id: UUID
    device_id: UUID
    timestamp: datetime
    metric_name: str
    metric_value: float
    unit: str | None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class TelemetryQuarantineResponse(BaseModel):
    """A quarantined telemetry reading awaiting model fix-up or review."""

    id: UUID
    device_id: UUID
    received_at: datetime
    metric_name: str
    metric_value: float | None
    raw_payload: dict[str, Any]
    reason: str

    model_config = ConfigDict(from_attributes=True)


class LocationPayload(BaseModel):
    """Standalone location update on `…/location` topic."""

    device_id: UUID
    timestamp: datetime
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_m: float | None = Field(default=None, ge=0.0)
    source: LocationSource = "gps"


class DeviceEventPayload(BaseModel):
    """Free-form device-side event on `…/events` topic."""

    device_id: UUID
    timestamp: datetime
    event_type: str = Field(min_length=1, max_length=100)
    details: dict[str, Any] | None = None



# ---------------------------------------------------------------------------
# Sprint 15 — Sites & Zones
# ---------------------------------------------------------------------------


class SiteCreate(BaseModel):
    """Create a site."""

    name: str = Field(min_length=1, max_length=255)
    address: str | None = None
    default_timezone: str = Field(default="UTC", max_length=64)
    metadata: dict[str, Any] | None = None


class SiteUpdate(BaseModel):
    """Patch a site."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = None
    default_timezone: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] | None = None


class SiteResponse(BaseModel):
    """Persisted site row."""

    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    default_timezone: str
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ZoneCreate(BaseModel):
    """Create a reader-bound zone (geofence kind reserved for Sprint 17a)."""

    site_id: UUID
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["reader_bound", "geofence"] = Field(default="reader_bound")
    fixed_reader_ids: list[UUID] | None = None
    polygon_geojson: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_kind_payload(self) -> "ZoneCreate":
        if self.kind == "reader_bound" and not self.fixed_reader_ids:
            raise ValueError(
                "reader_bound zones require a non-empty fixed_reader_ids"
            )
        if self.kind == "geofence" and not self.polygon_geojson:
            raise ValueError("geofence zones require polygon_geojson")
        return self


class ZoneUpdate(BaseModel):
    """Patch a zone."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    fixed_reader_ids: list[UUID] | None = None
    polygon_geojson: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_fixed_readers(self) -> "ZoneUpdate":
        # Empty list would later trip ck_zones_kind_payload at the DB layer
        # for reader_bound zones; reject up-front with a clear 422.
        if (
            "fixed_reader_ids" in self.model_fields_set
            and self.fixed_reader_ids is not None
            and len(self.fixed_reader_ids) == 0
        ):
            raise ValueError(
                "fixed_reader_ids must be omitted or contain at least one reader"
            )
        return self


class ZoneResponse(BaseModel):
    """Persisted zone row."""

    id: UUID
    tenant_id: UUID
    site_id: UUID
    name: str
    kind: str
    fixed_reader_ids: list[UUID] | None = None
    polygon_geojson: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SubjectZoneChanged(BaseModel):
    """Payload for the ``subject.zone_changed`` event (Sprint 15)."""

    tenant_id: UUID
    subject_kind: str  # 'asset' | 'stock_item'
    subject_id: UUID
    from_zone_id: UUID | None
    to_zone_id: UUID | None
    device_id: UUID
    tag_id: str | None
    epc: str | None = None
    tid: str | None = None
    timestamp: datetime


# -- Sprint 15 Phase B: Assets & Tag Bindings --


class AssetCreate(BaseModel):
    """Create an asset."""

    name: str = Field(min_length=1, max_length=255)
    asset_type: str = Field(min_length=1, max_length=50)
    external_ref: str | None = Field(default=None, max_length=255)
    status: Literal["active", "retired", "lost"] = Field(default="active")
    parent_asset_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class AssetUpdate(BaseModel):
    """Patch an asset."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    asset_type: str | None = Field(default=None, min_length=1, max_length=50)
    external_ref: str | None = Field(default=None, max_length=255)
    status: Literal["active", "retired", "lost"] | None = None
    parent_asset_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class AssetResponse(BaseModel):
    """Persisted asset row."""

    id: UUID
    tenant_id: UUID
    external_ref: str | None
    name: str
    asset_type: str
    status: str
    parent_asset_id: UUID | None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetTagBindingCreate(BaseModel):
    """Bind a tag value to an asset."""

    binding_value: str = Field(min_length=1, max_length=256)
    binding_kind: Literal["epc", "tid", "device"] = Field(default="epc")
    metadata: dict[str, Any] | None = None


class AssetTagBindingResponse(BaseModel):
    """Persisted asset_tag_bindings row."""

    id: UUID
    tenant_id: UUID
    asset_id: UUID
    binding_value: str
    binding_kind: str
    bound_at: datetime
    unbound_at: datetime | None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class TagCollisionResponse(BaseModel):
    """Cross-tenant collision report for a binding_value (admin only).

    Never reveals other tenants' identities — only the count.
    """

    binding_value: str
    other_tenant_count: int


# -------- External locations (Sprint 15 Phase C) --------


class ExternalLocationCreate(BaseModel):
    """Inbound non-RFID position fix for an asset."""

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    recorded_at: datetime
    source: str = Field(..., min_length=1, max_length=64)
    accuracy_meters: float | None = Field(None, ge=0.0)
    speed_kph: float | None = Field(None, ge=0.0)
    heading_deg: float | None = Field(None, ge=0.0, lt=360.0)
    metadata: dict[str, Any] | None = None


class ExternalLocationResponse(BaseModel):
    """Persisted external_locations row."""

    id: UUID
    tenant_id: UUID
    asset_id: UUID
    recorded_at: datetime
    latitude: float
    longitude: float
    source: str
    accuracy_meters: float | None
    speed_kph: float | None
    heading_deg: float | None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


# -------- Carrier semantics (Sprint 15 Phase C) --------


class AssetLoadRequest(BaseModel):
    """POST /assets/{id}/load — attach a child asset to a carrier."""

    parent_asset_id: UUID
    at: datetime | None = None  # defaults to server-side now()


class AssetUnloadRequest(BaseModel):
    """POST /assets/{id}/unload — detach a child asset from its carrier."""

    at: datetime | None = None


class ManifestEntry(BaseModel):
    """One node in an asset's containment tree."""

    asset_id: UUID
    name: str
    asset_type: str
    parent_asset_id: UUID | None
    depth: int
    children: list["ManifestEntry"] = Field(default_factory=list)


class ManifestResponse(BaseModel):
    """GET /assets/{id}/manifest — recursive children of a carrier."""

    asset_id: UUID
    name: str
    asset_type: str
    children: list[ManifestEntry] = Field(default_factory=list)


# -------- Asset location & path (Sprint 15 — view + path API) --------


class AssetCurrentLocation(BaseModel):
    """One row of the ``asset_current_location`` SQL view.

    The latest known position for an active asset binding, badged by source
    (`rfid` for the latest tag-read or one of the ``external_locations.source``
    strings — e.g. `samsara`, `geotab`, `manual` — for the latest external
    fix). Whichever side is newer wins.
    """

    asset_id: UUID
    recorded_at: datetime
    latitude: float
    longitude: float
    accuracy_meters: float | None
    device_id: UUID | None
    latest_position_source: str

    model_config = ConfigDict(from_attributes=True)


class AssetPathPoint(BaseModel):
    """One point on an asset's merged movement path.

    Sourced from either RFID tag reads (`source='rfid'`) or external position
    fixes (`source` matches the originating ``external_locations.source``).
    Returned in ascending chronological order.
    """

    recorded_at: datetime
    latitude: float
    longitude: float
    accuracy_meters: float | None
    source: str
    device_id: UUID | None = None
    tag_read_id: UUID | None = None
    external_id: UUID | None = None


class AssetInZoneSummary(BaseModel):
    """One row of `GET /zones/{zone_id}/assets` — assets currently in a zone."""

    asset_id: UUID
    name: str
    asset_type: str
    last_seen_at: datetime
    binding_value: str
    binding_kind: str


# ============================================================================
# Sprint 15b — Inventory tracking
# ============================================================================


class ProductCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    gtin: str | None = Field(default=None, max_length=14)
    name: str = Field(..., min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=64)
    unit: Literal["each", "case", "pallet"] = "each"
    attributes: dict[str, Any] | None = None


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    gtin: str | None = Field(default=None, max_length=14)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=64)
    unit: Literal["each", "case", "pallet"] | None = None
    attributes: dict[str, Any] | None = None


class ProductResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    sku: str
    gtin: str | None
    name: str
    category: str | None
    unit: str
    attributes: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LotCreate(BaseModel):
    lot_code: str = Field(..., min_length=1, max_length=64)
    manufactured_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class LotUpdate(BaseModel):
    lot_code: str | None = Field(default=None, min_length=1, max_length=64)
    manufactured_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class LotResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    lot_code: str
    manufactured_at: datetime | None
    expires_at: datetime | None
    metadata: dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockItemCreate(BaseModel):
    product_id: UUID
    lot_id: UUID | None = None
    binding_value: str = Field(..., min_length=1, max_length=256)
    binding_kind: Literal["epc", "tid"] = "epc"
    metadata: dict[str, Any] | None = None


class StockItemUpdate(BaseModel):
    state: (
        Literal["in_stock", "in_transit", "consumed", "expired", "lost"] | None
    ) = None
    lot_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class StockItemResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    lot_id: UUID | None
    binding_value: str
    binding_kind: str
    state: str
    current_zone_id: UUID | None
    first_seen_at: datetime
    last_seen_at: datetime
    consumed_at: datetime | None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class StockMovementResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    stock_item_id: UUID
    from_zone_id: UUID | None
    to_zone_id: UUID | None
    movement_type: str
    quantity: int
    device_id: UUID | None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockLevelRow(BaseModel):
    """One bucket from the ``stock_levels`` view."""

    product_id: UUID
    lot_id: UUID | None
    zone_id: UUID | None
    quantity: int


class TagDataMappingCreate(BaseModel):
    scope_kind: Literal["tenant", "product"]
    scope_id: UUID | None = None
    semantic_field: str = Field(..., min_length=1, max_length=40)
    tag_data_key: str = Field(..., min_length=1, max_length=64)
    transform: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _check_scope_consistency(self) -> "TagDataMappingCreate":
        # Mirrors the DB CHECK constraint so callers get a 422 instead of a 409.
        if self.scope_kind == "tenant" and self.scope_id is not None:
            raise ValueError("scope_id must be null when scope_kind='tenant'")
        if self.scope_kind != "tenant" and self.scope_id is None:
            raise ValueError(
                f"scope_id is required when scope_kind='{self.scope_kind}'"
            )
        return self


class TagDataMappingResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    scope_kind: str
    scope_id: UUID | None
    semantic_field: str
    tag_data_key: str
    transform: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
