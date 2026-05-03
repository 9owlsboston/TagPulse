"""Pydantic schemas for tag read messages, devices, and API responses."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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

