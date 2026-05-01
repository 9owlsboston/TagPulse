"""Event types for the edge agent.

These are *inputs* (what the hardware loop hands to the agent) and *outputs*
(what gets serialized onto the wire). Output schemas mirror the backend
Pydantic models in `src/tagpulse/models/schemas.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

EventKind = Literal["tag-reads", "telemetry", "location", "status", "events"]


# -- Inputs (from hardware loop) --


@dataclass(frozen=True)
class RawTagRead:
    """A single raw read off the antenna. ENTER/EXIT/dedup happens later."""

    tag_id: str
    antenna: str = "default"
    signal_strength: float | None = None
    observed_at: datetime | None = None
    sensor_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class SensorSample:
    """One reading from an on-Pi sensor (temp, humidity, battery, ...)."""

    metric_name: str
    value: float
    unit: str | None = None
    observed_at: datetime | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class LocationFix:
    """A GPS or other location fix."""

    latitude: float
    longitude: float
    accuracy_m: float | None = None
    source: Literal["gps", "fixed", "inferred"] = "gps"
    observed_at: datetime | None = None


# -- Outputs (queued / wire payloads) --


@dataclass
class OutboundEvent:
    """A serialized event awaiting publish."""

    kind: EventKind
    topic: str
    payload: dict[str, Any]
    enqueued_at: float  # monotonic seconds
    rowid: int | None = None  # set when persisted to the buffer

    # State counter for retry attribution; not transmitted.
    attempts: int = field(default=0)
