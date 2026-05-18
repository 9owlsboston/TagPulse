"""Unit tests for the signaling event-bus topic (Sprint 41 Phase B4/B5).

Validates that the new ``SIGNALING_ATTRIBUTION_SETTLED`` topic is
publishable through :class:`AsyncEventBus` and routes to subscribed
handlers \u2014 the same in-process bus mechanism Phase D's
OverlappingZones processor will use to fire ``signaling.location.on_inference``
rules. Phase B only needs the plumbing to be exercised; the actual
publisher (processor) and subscriber (rules engine on_inference handler)
land in Phase D.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Event, Topic


def test_signaling_attribution_settled_topic_exists() -> None:
    """Guard against accidental removal of the topic enum member."""
    assert Topic.SIGNALING_ATTRIBUTION_SETTLED.value == "signaling.attribution_settled"


def test_signaling_attribution_settled_in_topic_enum_iteration() -> None:
    """``for topic in Topic`` is how :func:`WebhookDispatcher` discovers
    topics to subscribe to. The new topic must appear in iteration so
    the webhook layer naturally picks it up without code changes."""
    assert Topic.SIGNALING_ATTRIBUTION_SETTLED in list(Topic)


@pytest.mark.asyncio
async def test_publish_and_subscribe_signaling_attribution_settled() -> None:
    """End-to-end: subscribe a handler, start the bus, publish an
    event, drain, assert the handler was called with the same event."""

    bus = AsyncEventBus(capacity=100, overflow="raise")
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe(Topic.SIGNALING_ATTRIBUTION_SETTLED, handler)
    await bus.start()

    event = Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=datetime.now(UTC),
        payload={
            "tenant_id": str(uuid4()),
            "asset_id": str(uuid4()),
            "zone_id": str(uuid4()),
            "confidence": 0.87,
            "aggregation_window_s": 60,
        },
    )
    await bus.publish(Topic.SIGNALING_ATTRIBUTION_SETTLED, event)
    # Wait a short tick for the consumer task to drain the queue.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    await bus.stop(timeout=2.0)

    assert len(received) == 1
    assert received[0].id == event.id
    assert received[0].payload["confidence"] == 0.87


@pytest.mark.asyncio
async def test_multiple_subscribers_receive_attribution_settled() -> None:
    """Multiple subscribers (e.g. rules engine on_inference + audit
    sink + future analytics) must all see each event \u2014 the rules
    engine must not starve siblings."""

    bus = AsyncEventBus(capacity=100, overflow="raise")
    a: list[Event] = []
    b: list[Event] = []

    async def handler_a(event: Event) -> None:
        a.append(event)

    async def handler_b(event: Event) -> None:
        b.append(event)

    await bus.subscribe(Topic.SIGNALING_ATTRIBUTION_SETTLED, handler_a)
    await bus.subscribe(Topic.SIGNALING_ATTRIBUTION_SETTLED, handler_b)
    await bus.start()

    event = Event(
        id=uuid4(),
        topic=Topic.SIGNALING_ATTRIBUTION_SETTLED,
        timestamp=datetime.now(UTC),
        payload={"confidence": 0.9},
    )
    await bus.publish(Topic.SIGNALING_ATTRIBUTION_SETTLED, event)
    for _ in range(50):
        if a and b:
            break
        await asyncio.sleep(0.01)
    await bus.stop(timeout=2.0)

    assert len(a) == 1
    assert len(b) == 1
