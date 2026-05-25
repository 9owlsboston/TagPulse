"""Telemetry ingestion service (Sprint 14).

Validates readings against per-tenant ``telemetry_models`` definitions, then
either persists them to ``telemetry_readings`` (subject_kind='device') or
routes them to ``telemetry_quarantine`` with a reason. Out-of-range readings
additionally emit a ``telemetry.out_of_range`` event for the rules engine to
consume.

Sprint 21 (ADR-015 §6): swapped the underlying repository from the now-
removed ``TimescaleTelemetryRepository`` to
:class:`TelemetryReadingsRepository`; the public service contract is
unchanged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from tagpulse.api.services.telemetry_model_service import TelemetryModelService
from tagpulse.core.otel_metrics import (
    device_events_counter,
    location_updates_counter,
    telemetry_ingestion_counter,
    telemetry_quarantined_counter,
)
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import (
    DeviceEventPayload,
    LocationPayload,
    TelemetryBatch,
    TelemetryQuarantineResponse,
    TelemetryReading,
    TelemetryResponse,
)
from tagpulse.repositories.protocols import (
    DeviceRepository,
    TelemetryReadingsRepository,
)

logger = logging.getLogger(__name__)

# ClockGuard preview from §6 of the design — promoted to middleware in Sprint 16.
MAX_AGE = timedelta(hours=24)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class QuarantineReason(StrEnum):
    UNKNOWN_METRIC = "unknown_metric"
    OUT_OF_RANGE = "out_of_range"
    UNIT_MISMATCH = "unit_mismatch"
    STALE_TIMESTAMP = "stale_timestamp"


class TelemetryService:
    """Validates and persists telemetry, location, and device events."""

    def __init__(
        self,
        repo: TelemetryReadingsRepository,
        event_bus: EventBus,
        model_service: TelemetryModelService,
        device_repo: DeviceRepository | None = None,
    ) -> None:
        self._repo = repo
        self._event_bus = event_bus
        self._model_service = model_service
        self._device_repo = device_repo

    async def ingest_batch(self, tenant_id: UUID, batch: TelemetryBatch) -> dict[str, int]:
        """Validate and persist a batch of readings. Returns counts."""
        device = (
            await self._device_repo.get(tenant_id, batch.device_id)
            if self._device_repo is not None
            else None
        )
        device_type = device.device_type if device else "rfid_reader"
        model = await self._model_service.get_by_device_type(tenant_id, device_type)
        metrics_by_name = {m.name: m for m in (model.metrics if model else [])}

        accepted = 0
        quarantined = 0
        for reading in batch.readings:
            outcome = await self._process_reading(
                tenant_id, batch.device_id, reading, metrics_by_name
            )
            if outcome == "accepted":
                accepted += 1
            else:
                quarantined += 1
        return {"accepted": accepted, "quarantined": quarantined}

    async def ingest_reading(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        *,
        device_type: str | None = None,
    ) -> TelemetryResponse | None:
        """Single-reading entrypoint. Returns persisted row or ``None`` if quarantined."""
        if device_type is None and self._device_repo is not None:
            device = await self._device_repo.get(tenant_id, device_id)
            device_type = device.device_type if device else "rfid_reader"
        device_type = device_type or "rfid_reader"
        model = await self._model_service.get_by_device_type(tenant_id, device_type)
        metrics_by_name = {m.name: m for m in (model.metrics if model else [])}
        outcome, response = await self._process_reading_with_response(
            tenant_id, device_id, reading, metrics_by_name
        )
        return response if outcome == "accepted" else None

    async def ingest_location(self, tenant_id: UUID, payload: LocationPayload) -> None:
        """Persist a standalone location update as two metric rows (lat/lon)."""
        if not _timestamp_acceptable(payload.timestamp):
            logger.warning(
                "Dropping stale/future location for device %s ts=%s",
                payload.device_id,
                payload.timestamp,
            )
            return
        metadata = {
            "source": payload.source,
            "accuracy_m": payload.accuracy_m,
        }
        for metric_name, value in (
            ("location.latitude", payload.latitude),
            ("location.longitude", payload.longitude),
        ):
            response = await self._repo.insert_reading(
                tenant_id,
                payload.device_id,
                TelemetryReading(
                    timestamp=payload.timestamp,
                    metric_name=metric_name,
                    metric_value=value,
                    unit="deg",
                ),
                metadata=metadata,
            )
            await self._publish_telemetry_recorded(
                tenant_id, payload.device_id, response, source="device"
            )
        location_updates_counter.add(1, {"tenant_id": str(tenant_id)})

    async def ingest_device_event(self, tenant_id: UUID, payload: DeviceEventPayload) -> None:
        """Persist a device-side event. v1: log + count; richer storage in Sprint 16."""
        if not _timestamp_acceptable(payload.timestamp):
            logger.warning(
                "Dropping stale device event from %s ts=%s",
                payload.device_id,
                payload.timestamp,
            )
            return
        device_events_counter.add(
            1,
            {
                "tenant_id": str(tenant_id),
                "event_type": payload.event_type,
            },
        )
        logger.info(
            "Device event: tenant=%s device=%s type=%s",
            tenant_id,
            payload.device_id,
            payload.event_type,
        )

    async def _process_reading(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        metrics_by_name: dict[str, Any],
    ) -> str:
        outcome, _ = await self._process_reading_with_response(
            tenant_id, device_id, reading, metrics_by_name
        )
        return outcome

    async def _process_reading_with_response(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        metrics_by_name: dict[str, Any],
    ) -> tuple[str, TelemetryResponse | None]:
        if not _timestamp_acceptable(reading.timestamp):
            await self._quarantine(tenant_id, device_id, reading, QuarantineReason.STALE_TIMESTAMP)
            return "quarantined", None

        metric_def = metrics_by_name.get(reading.metric_name)
        if metric_def is None:
            await self._quarantine(tenant_id, device_id, reading, QuarantineReason.UNKNOWN_METRIC)
            return "quarantined", None

        # Range check
        if (metric_def.min_value is not None and reading.metric_value < metric_def.min_value) or (
            metric_def.max_value is not None and reading.metric_value > metric_def.max_value
        ):
            await self._quarantine(tenant_id, device_id, reading, QuarantineReason.OUT_OF_RANGE)
            await self._event_bus.publish(
                Topic.TELEMETRY_OUT_OF_RANGE,
                Event(
                    id=uuid.uuid4(),
                    topic=Topic.TELEMETRY_OUT_OF_RANGE,
                    timestamp=datetime.now(UTC),
                    payload={
                        "tenant_id": str(tenant_id),
                        "device_id": str(device_id),
                        "metric_name": reading.metric_name,
                        "metric_value": reading.metric_value,
                        "min_value": metric_def.min_value,
                        "max_value": metric_def.max_value,
                        "timestamp": reading.timestamp.isoformat(),
                    },
                ),
            )
            return "quarantined", None

        # Unit mismatch is enrichment, not rejection — log and continue.
        if reading.unit and metric_def.unit and reading.unit != metric_def.unit:
            logger.warning(
                "Unit mismatch for %s (got %s, expected %s) — enriching",
                reading.metric_name,
                reading.unit,
                metric_def.unit,
            )

        response = await self._repo.insert_reading(tenant_id, device_id, reading)
        telemetry_ingestion_counter.add(
            1,
            {"tenant_id": str(tenant_id), "metric_name": reading.metric_name},
        )
        await self._publish_telemetry_recorded(tenant_id, device_id, response, source="device")
        return "accepted", response

    async def query(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        metric_name: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TelemetryResponse]:
        """Query telemetry readings with filters."""
        return await self._repo.query(
            tenant_id,
            device_id=device_id,
            metric_name=metric_name,
            start=start,
            end=end,
            limit=limit,
        )

    async def list_quarantine(
        self,
        tenant_id: UUID,
        *,
        device_id: UUID | None = None,
        reason: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TelemetryQuarantineResponse]:
        """List quarantined telemetry rows for review."""
        return await self._repo.list_quarantine(
            tenant_id,
            device_id=device_id,
            reason=reason,
            limit=limit,
            offset=offset,
        )

    async def _quarantine(
        self,
        tenant_id: UUID,
        device_id: UUID,
        reading: TelemetryReading,
        reason: QuarantineReason,
    ) -> None:
        await self._repo.quarantine(tenant_id, device_id, reading, reason.value)
        telemetry_quarantined_counter.add(
            1,
            {
                "tenant_id": str(tenant_id),
                "metric_name": reading.metric_name,
                "reason": reason.value,
            },
        )

    async def _publish_telemetry_recorded(
        self,
        tenant_id: UUID,
        device_id: UUID,
        response: TelemetryResponse,
        *,
        source: str,
    ) -> None:
        """Sprint 20 audit fix: device-scoped writes also publish
        ``Topic.TELEMETRY_RECORDED`` so ``telemetry.threshold`` rules
        with ``subject_kind='device'`` fire for tag-borne metrics, the
        legacy MQTT ``devices/{id}/telemetry`` topic, and HTTP batch
        ingest. Best-effort — failure to publish must not roll back
        the persisted row.
        """
        try:
            await self._event_bus.publish(
                Topic.TELEMETRY_RECORDED,
                Event(
                    id=response.id,
                    topic=Topic.TELEMETRY_RECORDED,
                    timestamp=response.timestamp,
                    payload={
                        "tenant_id": str(tenant_id),
                        "subject_kind": "device",
                        "subject_id": str(device_id),
                        "metric_name": response.metric_name,
                        "metric_value": response.metric_value,
                        "unit": response.unit,
                        "device_id": str(device_id),
                        "source": source,
                        "timestamp": response.timestamp.isoformat(),
                    },
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "telemetry.recorded publish failed for device %s metric %s",
                device_id,
                response.metric_name,
            )


def _timestamp_acceptable(ts: datetime) -> bool:
    now = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = ts - now
    if delta > MAX_FUTURE_SKEW:
        return False
    return -delta <= MAX_AGE
