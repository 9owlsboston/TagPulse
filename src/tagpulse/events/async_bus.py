"""In-process EventBus backed by asyncio.Queue — Phase 1 implementation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from tagpulse.events.protocol import Event, EventBusFullError, EventHandler, Topic

logger = logging.getLogger(__name__)

# Lazy import to avoid circular imports at module level
_metrics_loaded = False
_eventbus_published: Any = None
_eventbus_consumed: Any = None
_eventbus_dropped: Any = None


def _load_metrics() -> None:
    global _metrics_loaded, _eventbus_published, _eventbus_consumed, _eventbus_dropped
    if _metrics_loaded:
        return
    try:
        from tagpulse.core.otel_metrics import (
            eventbus_consumed,
            eventbus_dropped,
            eventbus_published,
        )

        _eventbus_published = eventbus_published
        _eventbus_consumed = eventbus_consumed
        _eventbus_dropped = eventbus_dropped
        _metrics_loaded = True
    except Exception:
        _metrics_loaded = True  # Don't retry


class AsyncEventBus:
    """Capacity-limited async event bus using asyncio.Queue per topic."""

    def __init__(
        self,
        capacity: int = 10_000,
        overflow: str = "drop_oldest",
        dead_letter_factory: Any = None,
    ) -> None:
        self._capacity = capacity
        self._overflow = overflow
        self._queues: dict[Topic, asyncio.Queue[Event]] = {}
        self._handlers: dict[Topic, list[EventHandler]] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._drop_count: dict[Topic, int] = {}
        self._running = False
        self._dead_letter_factory = dead_letter_factory

    async def publish(self, topic: Topic, event: Event) -> None:
        queue = self._queues.get(topic)
        if queue is None:
            queue = asyncio.Queue(maxsize=self._capacity)
            self._queues[topic] = queue

        if queue.full():
            if self._overflow == "drop_oldest":
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                self._drop_count[topic] = self._drop_count.get(topic, 0) + 1
                logger.warning(
                    "EventBus: dropped oldest event on %s (total drops: %d)",
                    topic,
                    self._drop_count[topic],
                )
            elif self._overflow == "drop_newest":
                self._drop_count[topic] = self._drop_count.get(topic, 0) + 1
                logger.warning(
                    "EventBus: dropped new event on %s (total drops: %d)",
                    topic,
                    self._drop_count[topic],
                )
                return
            elif self._overflow == "raise":
                raise EventBusFullError(topic, queue.qsize())

        queue.put_nowait(event)
        _load_metrics()
        if _eventbus_published:
            _eventbus_published.add(1, {"topic": topic.value})

    async def subscribe(self, topic: Topic, handler: EventHandler) -> None:
        handlers = self._handlers.setdefault(topic, [])
        handlers.append(handler)

    async def unsubscribe(self, topic: Topic, handler: EventHandler) -> None:
        handlers = self._handlers.get(topic, [])
        with contextlib.suppress(ValueError):
            handlers.remove(handler)

    async def start(self) -> None:
        self._running = True
        for topic in self._handlers:
            if topic not in self._queues:
                self._queues[topic] = asyncio.Queue(maxsize=self._capacity)
            task = asyncio.create_task(self._consume(topic))
            self._tasks.append(task)
        logger.info("EventBus started with %d topic consumers", len(self._tasks))

    async def stop(self, timeout: float = 10.0) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)
        self._tasks.clear()
        logger.info("EventBus stopped")

    async def drain(self, timeout: float = 10.0) -> None:
        """Process remaining queued events, then stop."""
        self._running = False
        deadline = asyncio.get_event_loop().time() + timeout
        drained = 0
        for topic, queue in self._queues.items():
            handlers = self._handlers.get(topic, [])
            while not queue.empty() and asyncio.get_event_loop().time() < deadline:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                for handler in handlers:
                    try:
                        await handler(event)
                    except Exception:
                        logger.exception("EventBus drain: handler failed for %s", topic)
                drained += 1
        logger.info("EventBus drained %d events", drained)
        await self.stop(timeout=1.0)

    async def _consume(self, topic: Topic) -> None:
        queue = self._queues[topic]
        handlers = self._handlers.get(topic, [])
        while self._running:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            _load_metrics()
            if _eventbus_consumed:
                _eventbus_consumed.add(1, {"topic": topic.value})
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    error_tb = traceback.format_exc()
                    logger.exception(
                        "EventBus: handler %s failed for event %s on topic %s",
                        handler.__name__,
                        event.id,
                        topic,
                    )
                    await self._persist_dead_letter(topic, event, error_tb)

    async def _persist_dead_letter(self, topic: Topic, event: Event, error_message: str) -> None:
        """Write failed event to dead_letter_events table."""
        if self._dead_letter_factory is None:
            return
        try:
            from tagpulse.models.database import DeadLetterEventModel

            tenant_id_str = event.payload.get("tenant_id")
            tenant_uuid = uuid.UUID(tenant_id_str) if tenant_id_str else None
            async with self._dead_letter_factory() as session:
                row = DeadLetterEventModel(
                    id=uuid.uuid4(),
                    tenant_id=tenant_uuid,
                    topic=topic.value,
                    payload=event.payload,
                    error_message=error_message[:4000],
                    retry_count=0,
                    status="pending",
                    source="event_bus",
                    failed_at=datetime.now(UTC),
                )
                session.add(row)
                await session.commit()
        except Exception:
            logger.exception("Failed to persist dead letter event")
