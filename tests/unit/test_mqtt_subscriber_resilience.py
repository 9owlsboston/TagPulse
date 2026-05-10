"""Sprint 31 (#18, #19) — MQTT subscriber resilience.

Regression tests for the failure modes that took ingest fully offline on
2026-05-09: a single malformed publish on ``…/tag-reads`` raised out of
``_handle_tag_read`` and killed the subscriber task. The worker container
stayed "healthy" while ingest was silently dead until restart.

These tests pin down two contracts:

1. ``_handle_tag_read`` MUST NOT propagate exceptions for any of the
   payload shapes operators have published in the wild — JSON list,
   single dict containing ``device_id`` (mismatching the topic-derived
   id), garbage bytes — and the ingestion service MUST NOT be invoked
   for them.
2. The top-level ``run()`` message loop swallows handler exceptions and
   continues to consume the next message.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tagpulse.ingestion.mqtt_subscriber import MqttSubscriber


def _make_subscriber() -> MqttSubscriber:
    """Build a subscriber whose deps will explode if accidentally touched."""
    return MqttSubscriber(
        host="broker.example",
        port=1883,
        session_factory=MagicMock(
            side_effect=AssertionError("session_factory must not be reached")
        ),
        event_bus=MagicMock(),
        usage_meter=None,
    )


def _msg(topic: str, payload: bytes | str) -> Any:
    return SimpleNamespace(
        topic=topic,
        payload=payload if isinstance(payload, bytes) else payload.encode(),
    )


@pytest.mark.asyncio
async def test_handle_tag_read_swallows_json_list_payload() -> None:
    """Issue #18 repro (a): array body — `**payload` raised TypeError."""
    sub = _make_subscriber()
    tenant = uuid4()
    device = uuid4()
    body = json.dumps([{"tag_id": "X", "timestamp": "2026-05-09T17:37:00Z"}])
    # Without the fix, this raises TypeError out of _handle_tag_read and
    # kills the message loop. The fix unwraps lists so each element is
    # validated individually; bad schema (no device_id field on the
    # body) becomes a per-item drop, not a process-killer.
    sub._build_ingestion_service = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("ingestion must not run for malformed input")
    )
    # Schema is actually valid here (just missing device_id, which the
    # subscriber injects from the topic), so we DO expect ingestion to
    # be invoked. Use a real ingestion stub instead.
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    sub._session_factory = MagicMock(return_value=fake_session)  # type: ignore[method-assign]
    fake_ingest = SimpleNamespace(ingest=AsyncMock())
    sub._build_ingestion_service = MagicMock(return_value=fake_ingest)  # type: ignore[method-assign]

    await sub._handle_tag_read(tenant, device, _msg("t/x/devices/y/tag-reads", body))

    # Single element in the array → single ingest call.
    assert fake_ingest.ingest.await_count == 1
    (called_tenant, called_read), _ = fake_ingest.ingest.await_args
    assert called_tenant == tenant
    assert called_read.device_id == device
    assert called_read.tag_id == "X"


@pytest.mark.asyncio
async def test_handle_tag_read_strips_device_id_from_body() -> None:
    """Issue #18 repro (b): body carrying ``device_id`` — duplicate kwarg."""
    sub = _make_subscriber()
    tenant = uuid4()
    topic_device = uuid4()
    body_device = uuid4()  # Intentionally different from topic.
    body = json.dumps(
        {
            "device_id": str(body_device),
            "tag_id": "Y",
            "timestamp": "2026-05-09T17:37:00Z",
        }
    )
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    sub._session_factory = MagicMock(return_value=fake_session)  # type: ignore[method-assign]
    fake_ingest = SimpleNamespace(ingest=AsyncMock())
    sub._build_ingestion_service = MagicMock(return_value=fake_ingest)  # type: ignore[method-assign]

    # Without the fix this raises ``TypeError: got multiple values for
    # keyword argument 'device_id'``.
    await sub._handle_tag_read(tenant, topic_device, _msg("t/x/devices/y/tag-reads", body))

    assert fake_ingest.ingest.await_count == 1
    (_, called_read), _ = fake_ingest.ingest.await_args
    # Topic-derived device_id wins; body's device_id is stripped, so a
    # misrouted publisher cannot smuggle ingest under another device.
    assert called_read.device_id == topic_device
    assert called_read.device_id != body_device


@pytest.mark.asyncio
async def test_handle_tag_read_swallows_garbage_bytes() -> None:
    """Issue #18 repro (c): not even valid JSON."""
    sub = _make_subscriber()
    sub._build_ingestion_service = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("ingestion must not run for garbage input")
    )
    sub._session_factory = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("session must not open for garbage input")
    )

    await sub._handle_tag_read(
        uuid4(), uuid4(), _msg("t/x/devices/y/tag-reads", b"\x00\x01not-json")
    )


@pytest.mark.asyncio
async def test_handle_tag_read_swallows_non_object_non_array_payload() -> None:
    """A bare JSON scalar (e.g. ``"hello"``) must not crash the loop."""
    sub = _make_subscriber()
    sub._build_ingestion_service = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("ingestion must not run for scalar input")
    )
    sub._session_factory = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("session must not open for scalar input")
    )

    await sub._handle_tag_read(
        uuid4(), uuid4(), _msg("t/x/devices/y/tag-reads", json.dumps("hello"))
    )


@pytest.mark.asyncio
async def test_run_loop_swallows_handler_exceptions() -> None:
    """The top-level loop must keep consuming after a handler crash."""
    sub = _make_subscriber()

    handled: list[str] = []

    async def fake_handle(message: Any) -> None:
        handled.append(str(message.topic))
        if message.topic == "boom":
            raise RuntimeError("simulated handler crash")

    sub._handle_message = fake_handle  # type: ignore[method-assign]

    messages = [
        _msg("ok-1", b"{}"),
        _msg("boom", b"{}"),
        _msg("ok-2", b"{}"),
    ]

    class FakeMessages:
        def __aiter__(self) -> AsyncIterator[Any]:
            async def gen() -> AsyncIterator[Any]:
                for m in messages:
                    yield m

            return gen()

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = None
    fake_client.subscribe = AsyncMock()
    fake_client.messages = FakeMessages()

    with patch("tagpulse.ingestion.mqtt_subscriber.aiomqtt.Client", return_value=fake_client):
        # The loop terminates when the async iterator is exhausted, so
        # we don't need a cancel — but keep a timeout as a safety net.
        await asyncio.wait_for(sub.run(), timeout=2.0)

    # All three messages were dispatched; the boom didn't stop the loop.
    assert handled == ["ok-1", "boom", "ok-2"]
