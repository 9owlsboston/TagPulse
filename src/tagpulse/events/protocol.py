"""EventBus protocol, event types, and topic definitions."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


class Topic(StrEnum):
    """Internal event topics."""

    TAG_READ_CREATED = "tag_read.created"
    DEVICE_STATUS_CHANGED = "device.status_changed"
    DEVICE_REGISTERED = "device.registered"
    DEVICE_DECOMMISSIONED = "device.decommissioned"
    ALERT_TRIGGERED = "alert.triggered"
    TELEMETRY_OUT_OF_RANGE = "telemetry.out_of_range"
    SUBJECT_ZONE_CHANGED = "subject.zone_changed"
    ASSET_LOADED = "asset.loaded"
    ASSET_UNLOADED = "asset.unloaded"
    EXTERNAL_LOCATION_RECORDED = "asset.external_location_recorded"
    # Sprint 20: subject-scoped telemetry write notifications. Published by
    # ``IngestionService`` (tag fan-out) and the ``POST /telemetry/readings/ingest``
    # admin endpoint after a row has landed in ``telemetry_readings``. The
    # rules engine consumes this for ``telemetry.threshold`` evaluation; the
    # legacy device-scoped path keeps using ``Topic.TAG_READ_CREATED`` so the
    # Sprint 14 contract is unchanged.
    TELEMETRY_RECORDED = "telemetry.recorded"


@dataclasses.dataclass(frozen=True)
class Event:
    """An internal platform event."""

    id: UUID
    topic: Topic
    timestamp: datetime
    payload: dict[str, Any]


EventHandler = Callable[[Event], Awaitable[None]]


class EventBusFullError(Exception):
    """Raised when the EventBus queue is at capacity and overflow policy is 'raise'."""

    def __init__(self, topic: Topic, queue_size: int) -> None:
        self.topic = topic
        self.queue_size = queue_size
        super().__init__(f"EventBus queue full for topic {topic} (size={queue_size})")


class EventBus(Protocol):
    """Technology-agnostic internal event bus contract."""

    async def publish(self, topic: Topic, event: Event) -> None: ...

    async def subscribe(self, topic: Topic, handler: EventHandler) -> None: ...

    async def unsubscribe(self, topic: Topic, handler: EventHandler) -> None: ...

    async def start(self) -> None: ...

    async def stop(self, timeout: float = 10.0) -> None: ...
