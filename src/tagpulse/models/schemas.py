"""Pydantic schemas for tag read messages, devices, and API responses."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# -- Tag Reads --


class TagReadCreate(BaseModel):
    """Incoming tag read event — used by both HTTP and MQTT ingestion paths."""

    device_id: UUID
    tag_id: str = Field(min_length=1, max_length=256)
    timestamp: datetime
    signal_strength: float | None = None
    sensor_data: dict[str, Any] | None = None


class TagReadResponse(BaseModel):
    """Tag read event returned from the API."""

    id: UUID
    device_id: UUID
    tag_id: str
    timestamp: datetime
    signal_strength: float | None
    sensor_data: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


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
