"""Pydantic schemas for tag read messages, devices, and API responses."""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LocationSource = Literal["gps", "fixed", "inferred", "reader_gnss"]

# -- Sprint 34 (gap 2.8): external_ref must avoid characters that are
# unsafe in URLs / shell paths / CSV exports. Matches the reference
# design's IMPLEMENTATION-GAPS.md row 2.8 list. --
_EXTERNAL_REF_FORBIDDEN_RE = re.compile(r"[.:/?#\\\[\]@,|&!=$'*+;%]")


def _validate_external_ref(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if _EXTERNAL_REF_FORBIDDEN_RE.search(stripped):
        raise ValueError(
            "external_ref must not contain any of: . : / ? # \\ [ ] @ , | & ! = $ ' * + ; %"
        )
    return stripped


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
    token_prefix: str | None = None
    token_rotated_at: datetime | None = None
    cert_thumbprint: str | None = None
    cert_subject: str | None = None
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
    """Define the telemetry schema for a subject (Sprint 14 → Sprint 18).

    ``device_type`` is required when ``subject_kind='device'`` (the
    original Sprint 14 case) and must be omitted otherwise. The DB
    enforces the same rule via ``ck_telemetry_models_device_type_required``.
    """

    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"] = "device"
    device_type: str | None = Field(default=None, min_length=1, max_length=50)
    metrics: list[MetricDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_device_type(self) -> "TelemetryModelCreate":
        if self.subject_kind == "device" and self.device_type is None:
            raise ValueError("device_type is required when subject_kind='device'")
        if self.subject_kind != "device" and self.device_type is not None:
            raise ValueError("device_type must be omitted when subject_kind != 'device'")
        return self


class TelemetryModelUpdate(BaseModel):
    """Sprint 28 G1: PATCH-style update for a telemetry model.

    Only ``metrics`` is mutable. ``subject_kind`` and ``device_type`` define
    the model's identity (the Sprint 18 unique constraint
    ``ix_telemetry_models_tenant_subject`` keys on these), so changing them
    via PATCH would amount to creating a different row — callers should
    DELETE + POST instead.
    """

    metrics: list[MetricDefinition] = Field(min_length=1)


class TelemetryModelResponse(BaseModel):
    """Telemetry model definition returned from the API."""

    id: UUID
    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"] = "device"
    device_type: str | None = None
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
    """A quarantined telemetry reading awaiting model fix-up or review.

    Sprint 18 added optional ``subject_kind`` / ``subject_id`` fields:
    legacy back-filled rows leave them ``None``; multi-subject ingest
    (Sprint 19) populates them so reviewers can see *what* the failed
    reading was meant to describe.
    """

    id: UUID
    device_id: UUID
    received_at: datetime
    metric_name: str
    metric_value: float | None
    raw_payload: dict[str, Any]
    reason: str
    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"] | None = None
    subject_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class TelemetryReadingResponse(BaseModel):
    """A persisted ``telemetry_readings`` row (Sprint 18).

    The subject-scoped successor to :class:`TelemetryResponse`. Carries
    the resolved subject (kind + id), the reporting device when known,
    and the source vocabulary defined in
    :doc:`docs/design/rfid-tag-data-model` §D4.
    """

    id: UUID
    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"]
    subject_id: UUID
    device_id: UUID | None
    timestamp: datetime
    metric_name: str
    metric_value: float
    unit: str | None
    source: Literal["device", "tag", "external", "derived"]
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


# -- Sprint 19: subject-scoped ingest + aggregate + latest --


class TelemetryReadingIngest(BaseModel):
    """A single subject-scoped telemetry reading (HTTP / MQTT ingest).

    Same shape as :class:`TelemetryReading` plus the resolved subject
    fields the caller wants attribution against. ``device_id`` is the
    optional reporting device for cross-reference (e.g. the gateway
    that uplinked an external observation).
    """

    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"]
    subject_id: UUID
    timestamp: datetime
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: float
    unit: str | None = Field(default=None, max_length=20)
    source: Literal["device", "tag", "external", "derived"] = "external"
    device_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class TelemetryReadingsBatch(BaseModel):
    """Batched subject-scoped telemetry payload (HTTP / MQTT)."""

    readings: list[TelemetryReadingIngest] = Field(min_length=1, max_length=500)


class TelemetryAggregateBucket(BaseModel):
    """One bucket from ``/telemetry/aggregates``.

    Returned in chronological order. Backed by ``cagg_telemetry_1m`` or
    ``cagg_telemetry_1h`` depending on the requested ``bucket_seconds``;
    falls back to a live ``time_bucket`` over ``telemetry_readings`` for
    arbitrary intervals.
    """

    subject_kind: Literal["device", "asset", "lot", "stock_item", "zone"]
    subject_id: UUID
    metric_name: str
    bucket: datetime
    avg_value: float
    min_value: float
    max_value: float
    sample_count: int

    model_config = ConfigDict(from_attributes=True)


class LatestTelemetryEntry(BaseModel):
    """Most-recent reading for a single metric on a subject.

    Embedded on ``GET /assets/{id}`` and ``GET /lots/{id}`` (capped at
    the 5 most-recently-written metrics per subject) so callers do not
    have to issue a follow-up ``/telemetry/readings?...&limit=1`` for
    each metric they want to display.
    """

    metric_name: str
    metric_value: float
    unit: str | None = None
    timestamp: datetime
    source: Literal["device", "tag", "external", "derived"]


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

# -- Sprint 34 gap 2.7: site discriminator + structured address validators --

SiteKind = Literal["site", "transporter"]

_ISO_3166_ALPHA2_RE = re.compile(r"^[A-Z]{2}$")


def _normalise_country(value: str | None) -> str | None:
    """Uppercase + ISO 3166-1 alpha-2 shape check (DB CHECK mirrors this).

    Runs in ``mode="before"`` so it can normalise whitespace and case
    *before* Pydantic's ``max_length`` check on the field rejects the
    raw input.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("country must be a string")
    candidate = value.strip().upper()
    if not _ISO_3166_ALPHA2_RE.match(candidate):
        raise ValueError("country must be an ISO 3166-1 alpha-2 code (e.g. 'US')")
    return candidate


class SiteCreate(BaseModel):
    """Create a site."""

    name: str = Field(min_length=1, max_length=255)
    kind: SiteKind = "site"
    address: str | None = None
    # Structured address (Sprint 34 gap 2.7). All optional; pair them as
    # the operator has data.
    street_line1: str | None = Field(default=None, max_length=255)
    street_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=16)
    # Geolocation (Sprint 34 gap 2.7). Both-or-neither, validated below.
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    default_timezone: str = Field(default="UTC", max_length=64)
    metadata: dict[str, Any] | None = None

    _normalise_country = field_validator("country", mode="before")(
        lambda cls, v: _normalise_country(v)
    )

    @model_validator(mode="after")
    def _check_latlon_paired(self) -> "SiteCreate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class SiteUpdate(BaseModel):
    """Patch a site.

    All fields optional. ``kind`` is mutable (a transporter that becomes
    permanently parked can be reclassified as a site, and vice-versa).

    Geolocation paired-validation only fires when *both* fields appear
    in the patch payload — the underlying DB CHECK enforces the
    invariant at write time for partial updates.

    Fields backed by NOT-NULL DB columns (``name``, ``kind``,
    ``default_timezone``) are ``Optional`` only for *omission* from the
    patch payload. Explicit ``null`` for any of them is rejected at
    422 so the DB never sees the NULL and returns 500.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: SiteKind | None = None
    address: str | None = None
    street_line1: str | None = Field(default=None, max_length=255)
    street_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=16)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    default_timezone: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] | None = None

    _normalise_country = field_validator("country", mode="before")(
        lambda cls, v: _normalise_country(v)
    )

    @model_validator(mode="after")
    def _check_latlon_paired_if_both_set(self) -> "SiteUpdate":
        provided = self.model_fields_set
        lat_in = "latitude" in provided
        lon_in = "longitude" in provided
        if lat_in and lon_in and (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self

    @model_validator(mode="after")
    def _reject_explicit_null_for_not_null_fields(self) -> "SiteUpdate":
        # ``name`` / ``kind`` / ``default_timezone`` are NOT NULL in the
        # ``sites`` table. Optional here means "omit to leave unchanged",
        # *not* "send null to clear". Reject explicit null at 422 so it
        # never reaches the DB.
        provided = self.model_fields_set
        for field in ("name", "kind", "default_timezone"):
            if field in provided and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be set to null")
        return self


class SiteResponse(BaseModel):
    """Persisted site row."""

    id: UUID
    tenant_id: UUID
    name: str
    kind: SiteKind
    address: str | None
    street_line1: str | None
    street_line2: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    default_timezone: str
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ZoneCreate(BaseModel):
    """Create a zone.

    Three kinds (mirrors the DB ``ck_zones_kind_payload`` CHECK):

    * ``reader_bound`` — requires a non-empty ``fixed_reader_ids`` list.
    * ``geofence`` — requires ``polygon_geojson`` (a GeoJSON ``Polygon``).
    * ``virtual`` — admin-defined logical grouping (no readers, no polygon).
      Used for cross-cutting categories like ``Cold-chain``, ``FDA-controlled``,
      or ``Critical assets``. Must NOT carry ``fixed_reader_ids`` or
      ``polygon_geojson``.
    """

    site_id: UUID
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["reader_bound", "geofence", "virtual"] = Field(default="reader_bound")
    fixed_reader_ids: list[UUID] | None = None
    polygon_geojson: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_kind_payload(self) -> "ZoneCreate":
        if self.kind == "reader_bound" and not self.fixed_reader_ids:
            raise ValueError("reader_bound zones require a non-empty fixed_reader_ids")
        if self.kind == "geofence" and not self.polygon_geojson:
            raise ValueError("geofence zones require polygon_geojson")
        if self.kind == "virtual" and (self.fixed_reader_ids or self.polygon_geojson):
            raise ValueError("virtual zones must not have fixed_reader_ids or polygon_geojson")
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
            raise ValueError("fixed_reader_ids must be omitted or contain at least one reader")
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
    bbox_min_lat: float | None = None
    bbox_max_lat: float | None = None
    bbox_min_lon: float | None = None
    bbox_max_lon: float | None = None
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
    """Create an asset.

    Sprint 41 Phase H (ADR 019 close-out): ``category_id`` is now
    **required** — every asset must point at a Category. The legacy
    ``asset_type`` String field has been dropped.
    """

    name: str = Field(min_length=1, max_length=255)
    external_ref: str | None = Field(default=None, max_length=255)
    status: Literal["active", "retired", "lost"] = Field(default="active")
    parent_asset_id: UUID | None = None
    category_id: UUID
    metadata: dict[str, Any] | None = None

    _normalise_external_ref = field_validator("external_ref")(
        lambda cls, v: _validate_external_ref(v)
    )


class AssetUpdate(BaseModel):
    """Patch an asset."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    external_ref: str | None = Field(default=None, max_length=255)
    status: Literal["active", "retired", "lost"] | None = None
    parent_asset_id: UUID | None = None
    # Repoints the asset to a different Category. Setting it to
    # ``null`` is rejected at the API layer — every asset must keep a
    # Category FK after Sprint 41 Phase H.
    category_id: UUID | None = None
    metadata: dict[str, Any] | None = None

    _normalise_external_ref = field_validator("external_ref")(
        lambda cls, v: _validate_external_ref(v)
    )


class AssetResponse(BaseModel):
    """Persisted asset row."""

    id: UUID
    tenant_id: UUID
    external_ref: str | None
    name: str
    status: str
    parent_asset_id: UUID | None
    # Sprint 42 — category_id is a nullable FK in the DB (assets created
    # before categories existed, or assets the operator chose to leave
    # uncategorised, both return NULL). Sprint 41 Phase H tightened this
    # to `UUID` (non-null) when it dropped `asset_type`, which would 500
    # at response-serialisation time for any uncategorised row. Restore
    # the nullability marker to match the DB and the AssetUpdate "clear"
    # semantics (PATCH with category_id: null).
    category_id: UUID | None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    # -- Sprint 19: optional embed for the most recent telemetry per
    # metric on this asset (capped server-side at 5 entries). Populated
    # only by ``GET /assets/{id}`` when the tenant has opted into
    # subject_kind='asset' telemetry; ``None`` otherwise. --
    latest_telemetry: list[LatestTelemetryEntry] | None = None

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
    parent_asset_id: UUID | None
    depth: int
    children: list["ManifestEntry"] = Field(default_factory=list)


class ManifestResponse(BaseModel):
    """GET /assets/{id}/manifest — recursive children of a carrier."""

    asset_id: UUID
    name: str
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
    # -- Sprint 19: optional embed for the most recent telemetry per
    # metric on this lot (capped server-side at 5 entries). Populated
    # only by ``GET /lots/{id}`` when the tenant has opted into
    # subject_kind='lot' telemetry; ``None`` otherwise. --
    latest_telemetry: list[LatestTelemetryEntry] | None = None

    model_config = ConfigDict(from_attributes=True)


class StockItemCreate(BaseModel):
    product_id: UUID
    lot_id: UUID | None = None
    parent_stock_item_id: UUID | None = None
    binding_value: str = Field(..., min_length=1, max_length=256)
    binding_kind: Literal["epc", "tid"] = "epc"
    metadata: dict[str, Any] | None = None


class StockItemUpdate(BaseModel):
    state: Literal["in_stock", "in_transit", "consumed", "expired", "lost"] | None = None
    lot_id: UUID | None = None
    parent_stock_item_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class StockItemResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    lot_id: UUID | None
    parent_stock_item_id: UUID | None = None
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


class StockMovementCreate(BaseModel):
    """Manual stock adjustment (Sprint 27 B2)."""

    product_id: UUID
    lot_id: UUID | None = None
    zone_id: UUID | None = None
    movement_type: Literal["enter", "exit", "adjustment"]
    quantity: int = Field(default=1, ge=1)
    reason: str = Field(..., min_length=1, max_length=500)
    stock_item_id: UUID | None = None


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
            raise ValueError(f"scope_id is required when scope_kind='{self.scope_kind}'")
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


class TagDataMappingUpdate(BaseModel):
    """Partial update for a tag-data-mapping (Sprint 27 B4)."""

    semantic_field: str | None = Field(default=None, min_length=1, max_length=40)
    tag_data_key: str | None = Field(default=None, min_length=1, max_length=64)
    transform: str | None = Field(default=None, max_length=40)


# -- Sprint 34 (ADR 019): Categories --

CategoryType = Literal["liquid_container", "reference_tag", "rti_container", "object"]


class CategoryCreate(BaseModel):
    """Create a category."""

    name: str = Field(min_length=1, max_length=255)
    sku_upc: str | None = Field(default=None, max_length=64)
    description: str | None = None
    category_type: CategoryType
    required_tags: int = Field(default=1, ge=1)


class CategoryUpdate(BaseModel):
    """Patch a category.

    ``category_type`` is intentionally absent — it is immutable after
    create per ADR 019. Attempts to send it must be rejected by the
    API layer (Pydantic will silently drop it without ``model_extra``
    enabled, so we surface a 400 there instead).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    sku_upc: str | None = Field(default=None, max_length=64)
    description: str | None = None
    required_tags: int | None = Field(default=None, ge=1)


class CategoryResponse(BaseModel):
    """Persisted category row."""

    id: UUID
    tenant_id: UUID
    name: str
    sku_upc: str | None
    description: str | None
    category_type: CategoryType
    required_tags: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -- Sprint 35 (ADR 020): Labels first-class --

LabelEntityType = Literal["asset", "site", "zone", "device", "category", "tag"]

# Patterns mirror the DB CHECK constraints created in migration 039.
# Keep these in lockstep with ``migrations/versions/039_labels_catalog.py``;
# if you change one you must change the other or seed will start failing
# with SQLSTATE 23514. ``$`` inside the key char-class is a literal ``$``
# (no anchor semantics inside ``[]``).
_LABEL_KEY_PATTERN = r"^[A-Za-z0-9_.+$]{3,24}$"
_LABEL_VALUE_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
_LABEL_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class LabelCreate(BaseModel):
    """Create a label catalog row."""

    entity_type: LabelEntityType
    key: str = Field(min_length=3, max_length=24, pattern=_LABEL_KEY_PATTERN)
    color: str | None = Field(default=None, pattern=_LABEL_COLOR_PATTERN)


class LabelUpdate(BaseModel):
    """Patch a label catalog row.

    ``entity_type`` is intentionally absent — it is immutable after
    create per ADR 020. The router separately rejects any smuggled
    attempt with a 400. Pydantic drops unknown fields by default so
    a benign omission is silent; the explicit router guard surfaces
    the policy.
    """

    key: str | None = Field(default=None, min_length=3, max_length=24, pattern=_LABEL_KEY_PATTERN)
    color: str | None = Field(default=None, pattern=_LABEL_COLOR_PATTERN)


class LabelResponse(BaseModel):
    """Persisted label catalog row."""

    id: UUID
    tenant_id: UUID
    entity_type: LabelEntityType
    key: str
    color: str | None
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LabelAssociationCreate(BaseModel):
    """Associate a labeled value to an entity.

    Per ADR 020 §"API path deviation", the caller identifies the
    label by ``key`` (scoped to the URL's entity_type); the
    repository looks up the catalog row. A 404 is returned if no
    matching catalog row exists — there is no auto-create.
    """

    key: str = Field(min_length=3, max_length=24, pattern=_LABEL_KEY_PATTERN)
    value: str = Field(min_length=1, max_length=64, pattern=_LABEL_VALUE_PATTERN)


class LabelAssociationResponse(BaseModel):
    """A label-value pair associated to a specific entity.

    Joins the ``entity_labels`` row with its parent ``labels`` row so
    the client gets the displayable ``key`` / ``color`` along with the
    polymorphic ``entity_id`` and the stored ``value``.
    """

    label_id: UUID
    entity_id: UUID
    entity_type: LabelEntityType
    key: str
    value: str
    color: str | None
    created_by: UUID | None
    created_at: datetime


# -- Sprint 50 (ADR 028): Tag registry --

TagStatus = Literal["registered", "active", "retired", "defective", "transferred_out"]
TagSource = Literal["csv_import", "api", "backfill", "transfer_in"]
TagTransferStatus = Literal["requested", "completed", "failed"]

# Mirrors ck_tags_epc_hex_format in migration 043: canonical uppercase
# hex, no separators, 16-128 chars. The API helper ``normalize_epc_hex``
# upper-cases and strips before validation so operators can paste in
# lowercase or whitespace-padded values.
_EPC_HEX_PATTERN = r"^[0-9A-F]{16,128}$"


class TagCreate(BaseModel):
    """Create one tag registry row (Phase B, single-row path).

    ``source`` is constrained to operator-driven values — ``transfer_in``
    is reserved for the transfer-completion path that writes the row
    server-side, not via this endpoint.
    """

    epc_hex: str = Field(min_length=16, max_length=128, pattern=_EPC_HEX_PATTERN)
    source: Literal["csv_import", "api", "backfill"] = "api"
    metadata: dict[str, object] | None = None


class TagUpdate(BaseModel):
    """Patch a tag registry row.

    Only ``status`` and ``metadata`` are operator-mutable here. The
    ADR-028 status-transition rules are enforced in the service
    layer; the schema accepts the full enum so an admin can flip
    ``registered`` / ``active`` → ``retired`` / ``defective``. The
    registrar worker is the only writer that may set ``active`` and
    the transfer flow is the only writer that may set
    ``transferred_out`` — both rejected here in the service layer.

    ``epc_hex`` is immutable (it's the natural key). ``source``,
    ``first_seen_at``, ``last_seen_at``, ``gs1_uri`` are all
    system-owned. There is intentionally no ``batch_id`` field —
    batch grouping goes through the ``entity_labels`` API per
    ADR 028 OQ 5.
    """

    status: TagStatus | None = None
    metadata: dict[str, object] | None = None


class TagResponse(BaseModel):
    """Persisted tag registry row."""

    id: UUID
    tenant_id: UUID
    epc_hex: str
    gs1_uri: str | None
    status: TagStatus
    source: TagSource
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    metadata: dict[str, object] | None = Field(default=None, alias="metadata_")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TagImportRowError(BaseModel):
    """One invalid CSV row from ``POST /tags/import`` (Sprint 50 C1).

    Returned to the client as part of :class:`TagImportResult` when
    any row fails validation; per ADR 028 OQ 4 the import is
    all-or-nothing, so a non-empty ``errors`` list means *nothing*
    was written (regardless of ``dry_run``).

    ``row`` is the 1-based CSV row number *after* the header
    (matching what spreadsheet users see). ``epc_hex`` is the
    operator-supplied value, echoed back unmodified so they can
    grep their CSV; it's omitted only if the row had no ``epc_hex``
    column value at all.
    """

    row: int
    epc_hex: str | None = None
    error: str


class TagImportResult(BaseModel):
    """Outcome of ``POST /tags/import`` (Sprint 50 C1/C2/C3).

    Five branches the client must distinguish:

    - ``errors`` non-empty → 422; the CSV was rejected, nothing was
      written, ``rows_created`` and ``rows_skipped`` are both 0.
      ``token`` is null (the ADR 028 governance rule binds the token
      to a *valid* preview only).
    - ``dry_run=True`` and ``errors`` empty → 200; the CSV would
      have created ``rows_created`` rows. No rows were written.
      ``token``, ``expires_in``, and ``sample`` are populated so the
      operator can re-submit with ``?confirm=<token>``.
    - ``dry_run=False`` and ``confirm`` provided and ``errors``
      empty and ``rows_total`` < tenant threshold → 201; the CSV was
      written. ``rows_created`` + ``rows_skipped`` = ``rows_total``;
      ``rows_skipped`` counts EPCs that already existed for the
      tenant (treated as idempotent, not as errors). ``token``
      echoes the consumed token for audit traceability.
    - ``dry_run=False`` and ``confirm`` provided and ``errors``
      empty and ``rows_total`` >= tenant threshold → **202**; the
      CSV is *queued* for second-admin approval per ADR 028
      §Governance #4. ``requires_approval=True`` and ``pending_id``
      is set; ``rows_created`` / ``rows_skipped`` are 0 (nothing
      written yet). The second admin completes the op via
      ``POST /bulk-operations/{pending_id}/approve``.
    - A bad token (mismatched CSV, wrong tenant/user, expired)
      surfaces as 409 from the route, not via this schema.

    Per ADR 028 §"Governance" rule 2, ``confirm`` and ``dry_run`` are
    mutually exclusive and at least one must be set — the route
    rejects "bare" submits (no dry-run, no confirm) with 400.
    """

    rows_total: int
    rows_created: int
    rows_skipped: int
    dry_run: bool
    errors: list[TagImportRowError] = Field(default_factory=list)
    # Populated only on a successful dry-run (and on the matching
    # commit, where it echoes the consumed value for audit).
    token: str | None = None
    expires_in: int | None = None
    # First-N preview of the EPCs that would be / were written.
    # ADR 028 says ``sample`` so the operator can eyeball "did I
    # paste the right reel?" without scrolling 10 000 rows. Bound
    # at 10 in the route — schema accepts any length.
    sample: list[str] | None = None
    # Sprint 50 C3: two-person rule. Set on the 202 branch only.
    # ``pending_id`` is the UUID the second admin uses on
    # ``POST /bulk-operations/{pending_id}/approve``.
    requires_approval: bool | None = None
    pending_id: UUID | None = None


class TagTransferRequest(BaseModel):
    """Initiate a cross-tenant transfer of one or more EPCs.

    All EPCs in one request share a server-generated ``request_id``.
    The receiving tenant is identified by slug. Phase B creates rows
    in ``status='requested'`` only — the acknowledgement /
    completion path lands in a later phase.
    """

    to_tenant_slug: str = Field(min_length=1, max_length=64)
    epcs: list[str] = Field(min_length=1, max_length=1000)


class TagTransferResponse(BaseModel):
    """Persisted tag_transfer row (one EPC of a request)."""

    id: UUID
    request_id: UUID
    from_tenant_id: UUID
    to_tenant_id: UUID
    epc_hex: str
    status: TagTransferStatus
    failure_reason: str | None
    requested_by: UUID
    requested_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Sprint 50 C3: pending bulk operations (two-person rule)
# ---------------------------------------------------------------------------


class PendingBulkOperationResponse(BaseModel):
    """Persisted ``pending_bulk_operations`` row (ADR 028 §Governance #4).

    Surfaced on:

    - the **202** branch of ``POST /tags/import`` (via the
      ``pending_id`` field on :class:`TagImportResult`) — operator A
      learns "your import is queued for approval, here is the id";
    - ``GET /bulk-operations/{id}`` — operator B fetches the record
      they're being asked to approve;
    - ``POST /bulk-operations/{id}/approve`` and ``/reject`` —
      the response echoes the updated row.

    The CSV bytes themselves (``payload``) are deliberately **not**
    exposed — operator B sees ``row_count`` + ``sample`` (the same
    10-EPC preview operator A saw on dry-run) which is the
    intended review surface. ``content_hash`` is exposed for
    cross-system verification.
    """

    id: UUID
    tenant_id: UUID
    operation: str
    status: str
    requested_by: UUID | None
    decided_by: UUID | None
    content_hash: str
    row_count: int
    sample: list[str]
    request_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Sprint 50 C4: scope-required bulk mutations (ADR 028 §Governance #3)
# ---------------------------------------------------------------------------


class TagBulkScope(BaseModel):
    """Scope selector for a bulk PATCH or bulk retire.

    Per ADR 028 §Governance #3 every bulk mutation MUST carry one of:

    - ``labels`` — deep-object label filter; MUST include a non-empty
      ``batch`` key (other label keys may be present and are
      AND-combined). This is the "operators think in batches"
      contract — bulk ops scoped purely by some other label key
      would be hard to audit by reel.
    - ``epc_list`` — explicit list of 1..1000 canonical EPC hex
      values. The 1000 cap is the ADR-pinned blast-radius limit
      for the explicit-enumeration shape.

    Exactly one of ``labels`` / ``epc_list`` must be set.
    """

    labels: dict[str, str] | None = None
    epc_list: list[str] | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> "TagBulkScope":
        has_labels = self.labels is not None and len(self.labels) > 0
        has_epcs = self.epc_list is not None and len(self.epc_list) > 0
        if has_labels and has_epcs:
            raise ValueError("scope: provide exactly one of 'labels' or 'epc_list', not both")
        if not has_labels and not has_epcs:
            raise ValueError(
                "scope: must provide either 'labels' (with a 'batch' key) or 'epc_list'"
            )
        if has_labels:
            assert self.labels is not None
            if "batch" not in self.labels or not self.labels["batch"].strip():
                raise ValueError(
                    "scope.labels: must include a non-empty 'batch' key (ADR 028 §Governance #3)"
                )
        if has_epcs:
            assert self.epc_list is not None
            # Per-EPC format check; the route layer normalizes (upper +
            # strip) before this validator runs in the request lifecycle,
            # but we re-check defensively in case a future caller skips
            # the route normalization (e.g., calling the schema from a
            # background worker that constructs a request body directly).
            pattern = re.compile(_EPC_HEX_PATTERN)
            for epc in self.epc_list:
                if not pattern.match(epc):
                    raise ValueError(
                        f"scope.epc_list: {epc!r} is not canonical EPC hex"
                        " (uppercase, 16-128 chars, no separators)"
                    )
            if len(set(self.epc_list)) != len(self.epc_list):
                raise ValueError("scope.epc_list: duplicate EPC values")
        return self


class TagBulkPatchRequest(BaseModel):
    """Body for ``POST /tags/bulk-patch`` (Sprint 50 C4, ADR 028).

    At least one of ``status`` / ``metadata`` must be set — the
    server has nothing else to apply otherwise. Per-tag status
    transitions are validated server-side against the same edge
    list :class:`TagUpdate` uses, so an operator can't slip
    ``transferred_out`` past the bulk surface.

    ``metadata`` is a full **replace** of the target row's
    metadata column (mirroring single-row PATCH semantics).
    Per-key merge can be added in a later sprint if customer
    feedback asks for it; doing replace-by-default avoids
    surprising "I cleared one key and the others stayed".
    """

    scope: TagBulkScope
    status: TagStatus | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _at_least_one_mutation(self) -> "TagBulkPatchRequest":
        if self.status is None and self.metadata is None:
            raise ValueError("bulk patch: at least one of 'status' or 'metadata' must be set")
        return self


class TagBulkRetireRequest(BaseModel):
    """Body for ``POST /tags/bulk-retire`` (Sprint 50 C4, ADR 028).

    Equivalent to ``POST /tags/bulk-patch`` with ``status='retired'``
    but exposed as its own endpoint so the audit log carries a
    distinct action name (``tag.bulk_retired`` vs
    ``tag.bulk_patched``) — operators searching "who retired
    batch X" don't have to filter PATCH calls by status payload.

    ``reason`` is optional free-text recorded on the audit-log
    entry; not persisted on the tag rows themselves.
    """

    scope: TagBulkScope
    reason: str | None = Field(default=None, max_length=500)


class TagBulkRowError(BaseModel):
    """One per-tag failure from a bulk PATCH / retire dry-run or commit.

    Currently the only failure surfaced this way is a rejected
    status transition (e.g., ``transferred_out → retired``).
    Per ADR 028's all-or-nothing rule on bulk mutations, any
    non-empty error list aborts the operation — nothing is written
    even on a confirmed commit.
    """

    epc_hex: str
    error: str


class TagBulkOperationResult(BaseModel):
    """Outcome of ``POST /tags/bulk-patch`` or ``POST /tags/bulk-retire``.

    Five branches mirroring :class:`TagImportResult`:

    - ``errors`` non-empty → **422**; the scope resolved but at
      least one matched tag would violate its status-transition
      edge list. Nothing is written. No token is minted.
    - ``dry_run=True`` and ``errors`` empty → **200**; ``matched``
      is the number of tags the scope resolved to, ``updated`` is
      0 (nothing written), ``token`` + ``expires_in`` + ``sample``
      are populated for a follow-up ``?confirm=<token>``.
    - ``dry_run=False`` and ``confirm`` provided and ``matched``
      below tenant threshold → **200**; ``updated`` is the number
      of rows changed.
    - ``dry_run=False`` and ``confirm`` provided and ``matched``
      at or above tenant threshold → **202**; the op is queued
      for second-admin approval. ``requires_approval=True``,
      ``pending_id`` is set, ``updated=0``.
    - Bad token surfaces as 409 from the route, not via this
      schema.
    """

    matched: int
    updated: int
    request_id: UUID | None = None
    dry_run: bool
    errors: list[TagBulkRowError] = Field(default_factory=list)
    sample: list[str] = Field(default_factory=list)
    token: str | None = None
    expires_in: int | None = None
    requires_approval: bool | None = None
    pending_id: UUID | None = None


# ---------------------------------------------------------------------------
# Sprint 50 Phase E — reconciliation report rows (ADR 028 §Governance #5).
# ---------------------------------------------------------------------------


class RegisteredUnreadRow(BaseModel):
    """One row of the ``registered-unread`` reconciliation view.

    Tag is in ``status ∈ {registered, active}`` but either has
    never been observed (``last_seen_at IS NULL``) or its last
    observation is older than the requested staleness window.
    """

    tag_id: UUID
    epc_hex: str
    status: TagStatus
    source: TagSource
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime


class UnregisteredReadingRow(BaseModel):
    """One row of the ``unregistered-reading`` reconciliation view.

    Distinct ``tag_id`` from ``tag_reads`` with ``tag_known=FALSE``
    inside the lookback window. ``tag_id`` is the raw value the
    reader emitted (not necessarily a canonical EPC) — operators
    use this view to onboard real-world EPCs that arrived without
    a prior registry entry.
    """

    tag_id: str
    last_seen_at: datetime
    read_count: int


class BindingOnRetiredRow(BaseModel):
    """One row of the ``bindings-on-retired`` reconciliation view.

    A ``stock_items`` row whose EPC binding points to a registry
    tag in a terminal status (``retired`` / ``defective`` /
    ``transferred_out``) and that has not been consumed. Surfaces
    inventory carrying decommissioned tags so the operator can
    re-bind or consume.
    """

    stock_item_id: UUID
    epc_hex: str
    product_id: UUID
    lot_id: UUID | None
    stock_item_state: str
    tag_id: UUID
    tag_status: TagStatus
    tag_updated_at: datetime


class DashboardSummary(BaseModel):
    """Aggregate counts powering the operator landing page.

    Sprint 54 Phase 54.3 — one tenant-scoped read per page load.
    All counts are point-in-time at ``generated_at``; clients use
    that timestamp for cache-busting / staleness display on the
    KPI tiles. Field semantics:

    - ``devices_online`` — devices whose ``last_seen`` is within
      the last 5 minutes AND whose ``connection_state`` is
      ``'connected'``. Strict AND because the stringly-typed
      column drifts when the MQTT subscriber misses a disconnect.
    - ``devices_total`` — all devices for the tenant.
    - ``alerts_open_24h`` — alerts with ``status='open'`` and
      ``triggered_at > now() - 24h``.
    - ``reads_per_hour_now`` — ``tag_reads`` rows with
      ``timestamp > now() - 1h``.
    - ``assets_active`` — assets with ``status='active'``.
    - ``tag_transfers_in_flight`` — cross-tenant tag-ownership
      handoffs (ADR 028) in ``status='requested'``, counted on
      either side via ``from_tenant_id = :t OR to_tenant_id = :t``.
    - ``tag_recon_backlog`` — sum of the three ADR 028 §Governance
      #5 reconciliation views (registered-unread, unregistered-
      reading, bindings-on-retired) for the tenant. Operator
      hygiene worklist size; not an alert.
    - ``low_stock_count`` — distinct products with fewer than
      ``tenants.low_stock_threshold`` active stock_items
      (``state='in_stock' AND consumed_at IS NULL``). Default
      threshold 3; overridable via ``PATCH /tenant/config``.
    """

    generated_at: datetime
    devices_online: int
    devices_total: int
    alerts_open_24h: int
    reads_per_hour_now: int
    assets_active: int
    tag_transfers_in_flight: int
    tag_recon_backlog: int
    low_stock_count: int
