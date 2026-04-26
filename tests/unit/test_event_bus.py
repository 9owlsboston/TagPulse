"""Unit tests for the EventBus async implementation."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Event, EventBusFullError, Topic


class TestAsyncEventBus:
    async def test_publish_creates_queue(self) -> None:
        bus = AsyncEventBus(capacity=10)
        event = _make_event()
        await bus.publish(Topic.TAG_READ_CREATED, event)
        assert Topic.TAG_READ_CREATED in bus._queues
        assert bus._queues[Topic.TAG_READ_CREATED].qsize() == 1

    async def test_drop_oldest_on_overflow(self) -> None:
        bus = AsyncEventBus(capacity=2, overflow="drop_oldest")
        e1 = _make_event()
        e2 = _make_event()
        e3 = _make_event()
        await bus.publish(Topic.TAG_READ_CREATED, e1)
        await bus.publish(Topic.TAG_READ_CREATED, e2)
        await bus.publish(Topic.TAG_READ_CREATED, e3)  # should drop e1
        queue = bus._queues[Topic.TAG_READ_CREATED]
        assert queue.qsize() == 2
        assert bus._drop_count[Topic.TAG_READ_CREATED] == 1

    async def test_drop_newest_on_overflow(self) -> None:
        bus = AsyncEventBus(capacity=2, overflow="drop_newest")
        e1 = _make_event()
        e2 = _make_event()
        e3 = _make_event()
        await bus.publish(Topic.TAG_READ_CREATED, e1)
        await bus.publish(Topic.TAG_READ_CREATED, e2)
        await bus.publish(Topic.TAG_READ_CREATED, e3)  # should be dropped
        queue = bus._queues[Topic.TAG_READ_CREATED]
        assert queue.qsize() == 2
        assert bus._drop_count[Topic.TAG_READ_CREATED] == 1

    async def test_raise_on_overflow(self) -> None:
        bus = AsyncEventBus(capacity=1, overflow="raise")
        await bus.publish(Topic.TAG_READ_CREATED, _make_event())
        with pytest.raises(EventBusFullError):
            await bus.publish(Topic.TAG_READ_CREATED, _make_event())

    async def test_subscribe_and_consume(self) -> None:
        bus = AsyncEventBus(capacity=10)
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(Topic.TAG_READ_CREATED, handler)
        await bus.start()
        event = _make_event()
        await bus.publish(Topic.TAG_READ_CREATED, event)

        # Give consumer task time to process
        import asyncio
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].id == event.id
        await bus.stop()

    async def test_stop_is_idempotent(self) -> None:
        bus = AsyncEventBus(capacity=10)
        await bus.start()
        await bus.stop()
        await bus.stop()  # should not raise


def _make_event() -> Event:
    return Event(
        id=uuid4(),
        topic=Topic.TAG_READ_CREATED,
        timestamp=datetime.now(UTC),
        payload={"tag_id": "TEST"},
    )
