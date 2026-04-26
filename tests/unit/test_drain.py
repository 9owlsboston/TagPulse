"""Unit tests for EventBus drain functionality."""

from datetime import UTC, datetime
from uuid import uuid4

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Event, Topic


class TestEventBusDrain:
    async def test_drain_processes_queued_events(self) -> None:
        bus = AsyncEventBus(capacity=100)
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(Topic.TAG_READ_CREATED, handler)
        await bus.start()

        # Publish events
        for i in range(5):
            event = Event(
                id=uuid4(),
                topic=Topic.TAG_READ_CREATED,
                timestamp=datetime.now(UTC),
                payload={"index": i},
            )
            await bus.publish(Topic.TAG_READ_CREATED, event)

        # Give consumer time to process some
        import asyncio
        await asyncio.sleep(0.2)

        # Drain remaining
        await bus.drain(timeout=5.0)
        assert len(received) == 5

    async def test_drain_empty_queue(self) -> None:
        bus = AsyncEventBus(capacity=10)
        await bus.subscribe(Topic.TAG_READ_CREATED, lambda e: None)
        await bus.start()
        await bus.drain(timeout=1.0)
        # Should not raise
