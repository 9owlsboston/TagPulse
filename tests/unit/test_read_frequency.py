"""Unit tests for the ReadFrequencyModule."""

from datetime import UTC, datetime
from uuid import uuid4

from tagpulse.analytics.read_frequency import ReadFrequencyModule
from tagpulse.events.protocol import Event, Topic


class TestReadFrequencyCounter:
    async def test_on_event_increments_counter(self) -> None:
        module = ReadFrequencyModule(session_factory=None)  # type: ignore[arg-type]
        tid = str(uuid4())
        did = str(uuid4())
        event = _make_event(tid, did)
        await module.on_event(event)
        await module.on_event(event)
        assert module._counters[(tid, did)] == 2

    async def test_on_event_multiple_devices(self) -> None:
        module = ReadFrequencyModule(session_factory=None)  # type: ignore[arg-type]
        tid = str(uuid4())
        d1 = str(uuid4())
        d2 = str(uuid4())
        await module.on_event(_make_event(tid, d1))
        await module.on_event(_make_event(tid, d2))
        await module.on_event(_make_event(tid, d1))
        assert module._counters[(tid, d1)] == 2
        assert module._counters[(tid, d2)] == 1

    async def test_on_event_skips_missing_ids(self) -> None:
        module = ReadFrequencyModule(session_factory=None)  # type: ignore[arg-type]
        event = Event(
            id=uuid4(),
            topic=Topic.TAG_READ_CREATED,
            timestamp=datetime.now(UTC),
            payload={},
        )
        await module.on_event(event)
        assert len(module._counters) == 0


class TestReadFrequencyProperties:
    def test_name(self) -> None:
        module = ReadFrequencyModule(session_factory=None)  # type: ignore[arg-type]
        assert module.name == "read_frequency"

    def test_subscribed_topics(self) -> None:
        module = ReadFrequencyModule(session_factory=None)  # type: ignore[arg-type]
        assert module.subscribed_topics == [Topic.TAG_READ_CREATED]


def _make_event(tenant_id: str, device_id: str) -> Event:
    return Event(
        id=uuid4(),
        topic=Topic.TAG_READ_CREATED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": tenant_id,
            "device_id": device_id,
            "tag_id": "TAG001",
            "signal_strength": -45.0,
        },
    )
