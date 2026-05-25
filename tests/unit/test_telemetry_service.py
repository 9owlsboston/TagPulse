"""Unit tests for TelemetryService (Sprint 14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from tagpulse.api.services.telemetry_service import (
    QuarantineReason,
    TelemetryService,
)
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.schemas import (
    LocationPayload,
    MetricDefinition,
    TelemetryQuarantineResponse,
    TelemetryReading,
    TelemetryResponse,
)


class FakeEventBus(EventBus):
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))

    async def subscribe(self, topic: Topic, handler: Any) -> None:  # type: ignore[override]
        return None

    async def unsubscribe(self, topic: Topic, handler: Any) -> None:  # type: ignore[override]
        return None


class FakeRepo:
    def __init__(self) -> None:
        self.readings: list[tuple[Any, Any, TelemetryReading, dict[str, Any] | None]] = []
        self.quarantined: list[tuple[Any, Any, TelemetryReading, str]] = []

    async def insert_reading(
        self,
        tenant_id: Any,
        device_id: Any,
        reading: TelemetryReading,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TelemetryResponse:
        self.readings.append((tenant_id, device_id, reading, metadata))
        return TelemetryResponse(
            id=uuid4(),
            device_id=device_id,
            timestamp=reading.timestamp,
            metric_name=reading.metric_name,
            metric_value=reading.metric_value,
            unit=reading.unit,
            metadata=metadata or reading.metadata,
        )

    async def quarantine(
        self,
        tenant_id: Any,
        device_id: Any,
        reading: TelemetryReading,
        reason: str,
    ) -> None:
        self.quarantined.append((tenant_id, device_id, reading, reason))

    async def query(self, *args: Any, **kwargs: Any) -> list[TelemetryResponse]:
        return []

    async def list_quarantine(self, *args: Any, **kwargs: Any) -> list[TelemetryQuarantineResponse]:
        return []


class FakeModelService:
    def __init__(self, metrics: list[MetricDefinition]) -> None:
        self._metrics = metrics

    async def get_by_device_type(self, tenant_id: Any, device_type: str) -> Any:
        class _Model:
            metrics: list[MetricDefinition] = []

        m = _Model()
        m.metrics = self._metrics
        return m


def _make_service(
    metrics: list[MetricDefinition],
) -> tuple[TelemetryService, FakeRepo, FakeEventBus]:
    repo = FakeRepo()
    bus = FakeEventBus()
    svc = TelemetryService(
        repo=repo,  # type: ignore[arg-type]
        event_bus=bus,
        model_service=FakeModelService(metrics),  # type: ignore[arg-type]
    )
    return svc, repo, bus


@pytest.mark.asyncio
async def test_accepted_reading_persists_and_counts() -> None:
    metrics = [MetricDefinition(name="temperature", unit="C", min_value=-40, max_value=85)]
    svc, repo, bus = _make_service(metrics)
    reading = TelemetryReading(
        timestamp=datetime.now(UTC),
        metric_name="temperature",
        metric_value=22.5,
        unit="C",
    )
    result = await svc.ingest_reading(uuid4(), uuid4(), reading)
    assert result is not None
    assert len(repo.readings) == 1
    assert not repo.quarantined
    # Sprint 20 audit fix: accepted readings now publish
    # ``Topic.TELEMETRY_RECORDED`` so device-scoped ``telemetry.threshold``
    # rules can fire. Quarantine path still publishes only on
    # ``TELEMETRY_OUT_OF_RANGE``.
    from tagpulse.events.protocol import Topic

    assert len(bus.published) == 1
    topic, _event = bus.published[0]
    assert topic == Topic.TELEMETRY_RECORDED


@pytest.mark.asyncio
async def test_unknown_metric_quarantined() -> None:
    svc, repo, bus = _make_service([])  # no metric defs
    reading = TelemetryReading(
        timestamp=datetime.now(UTC),
        metric_name="mystery",
        metric_value=1.0,
        unit="x",
    )
    result = await svc.ingest_reading(uuid4(), uuid4(), reading)
    assert result is None
    assert len(repo.quarantined) == 1
    assert repo.quarantined[0][3] == QuarantineReason.UNKNOWN_METRIC.value
    assert not bus.published


@pytest.mark.asyncio
async def test_out_of_range_quarantined_and_event_published() -> None:
    metrics = [MetricDefinition(name="temperature", unit="C", min_value=0, max_value=10)]
    svc, repo, bus = _make_service(metrics)
    reading = TelemetryReading(
        timestamp=datetime.now(UTC),
        metric_name="temperature",
        metric_value=99.0,
        unit="C",
    )
    result = await svc.ingest_reading(uuid4(), uuid4(), reading)
    assert result is None
    assert repo.quarantined[0][3] == QuarantineReason.OUT_OF_RANGE.value
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic is Topic.TELEMETRY_OUT_OF_RANGE
    assert event.payload["metric_name"] == "temperature"
    assert event.payload["min_value"] == 0
    assert event.payload["max_value"] == 10


@pytest.mark.asyncio
async def test_stale_timestamp_quarantined() -> None:
    metrics = [MetricDefinition(name="temperature", unit="C")]
    svc, repo, _ = _make_service(metrics)
    reading = TelemetryReading(
        timestamp=datetime.now(UTC) - timedelta(days=2),
        metric_name="temperature",
        metric_value=22.0,
        unit="C",
    )
    result = await svc.ingest_reading(uuid4(), uuid4(), reading)
    assert result is None
    assert repo.quarantined[0][3] == QuarantineReason.STALE_TIMESTAMP.value


@pytest.mark.asyncio
async def test_unit_mismatch_still_accepted() -> None:
    metrics = [MetricDefinition(name="temperature", unit="C")]
    svc, repo, _ = _make_service(metrics)
    reading = TelemetryReading(
        timestamp=datetime.now(UTC),
        metric_name="temperature",
        metric_value=72.0,
        unit="F",  # mismatched
    )
    result = await svc.ingest_reading(uuid4(), uuid4(), reading)
    assert result is not None
    assert len(repo.readings) == 1
    assert not repo.quarantined


@pytest.mark.asyncio
async def test_location_writes_two_rows() -> None:
    svc, repo, _ = _make_service([])
    payload = LocationPayload(
        device_id=uuid4(),
        timestamp=datetime.now(UTC),
        latitude=47.6,
        longitude=-122.3,
        accuracy_m=5.0,
        source="gps",
    )
    await svc.ingest_location(uuid4(), payload)
    assert len(repo.readings) == 2
    metric_names = {row[2].metric_name for row in repo.readings}
    assert metric_names == {"location.latitude", "location.longitude"}
    # Metadata should carry source + accuracy on both rows.
    for row in repo.readings:
        assert row[3] is not None
        assert row[3]["source"] == "gps"
        assert row[3]["accuracy_m"] == 5.0


@pytest.mark.asyncio
async def test_query_delegates_to_repo() -> None:
    """TelemetryService.query forwards filters to the repo."""

    class _CapturingRepo(FakeRepo):
        last_kwargs: dict[str, Any] = {}

        async def query(self, *args: Any, **kwargs: Any) -> list[TelemetryResponse]:
            self.last_kwargs = kwargs
            return []

    repo = _CapturingRepo()
    svc = TelemetryService(
        repo=repo,  # type: ignore[arg-type]
        event_bus=FakeEventBus(),
        model_service=FakeModelService([]),  # type: ignore[arg-type]
    )
    tenant = uuid4()
    device = uuid4()
    start = datetime.now(UTC) - timedelta(hours=1)
    end = datetime.now(UTC)
    await svc.query(
        tenant,
        device_id=device,
        metric_name="temperature",
        start=start,
        end=end,
        limit=50,
    )
    assert repo.last_kwargs == {
        "device_id": device,
        "metric_name": "temperature",
        "start": start,
        "end": end,
        "limit": 50,
    }


@pytest.mark.asyncio
async def test_list_quarantine_delegates_to_repo() -> None:
    """TelemetryService.list_quarantine forwards filters to the repo."""

    class _CapturingRepo(FakeRepo):
        last_kwargs: dict[str, Any] = {}

        async def list_quarantine(
            self, *args: Any, **kwargs: Any
        ) -> list[TelemetryQuarantineResponse]:
            self.last_kwargs = kwargs
            return []

    repo = _CapturingRepo()
    svc = TelemetryService(
        repo=repo,  # type: ignore[arg-type]
        event_bus=FakeEventBus(),
        model_service=FakeModelService([]),  # type: ignore[arg-type]
    )
    tenant = uuid4()
    device = uuid4()
    await svc.list_quarantine(
        tenant,
        device_id=device,
        reason="out_of_range",
        limit=25,
        offset=10,
    )
    assert repo.last_kwargs == {
        "device_id": device,
        "reason": "out_of_range",
        "limit": 25,
        "offset": 10,
    }
