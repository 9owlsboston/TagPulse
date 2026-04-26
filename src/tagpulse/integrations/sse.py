"""SSE streaming endpoint — real-time event feed for connected consumers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from tagpulse.core.tenant_auth import Tenant, get_current_tenant

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])


@router.get("/integrations/stream")
async def stream_events(
    events: str = Query(default="tag_read.created,alert.triggered"),
    tenant: Tenant = Depends(get_current_tenant),
    request: Request = ...,  # type: ignore[assignment]
) -> StreamingResponse:
    """SSE endpoint — streams real-time events filtered by tenant and event type."""
    event_types = [e.strip() for e in events.split(",") if e.strip()]

    async def event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1000)

        # Register a handler that forwards matching events to this connection's queue
        from tagpulse.events.protocol import Event

        async def _forward(event: Event) -> None:
            if event.topic.value not in event_types:
                return
            event_tenant = event.payload.get("tenant_id")
            if event_tenant != str(tenant.id):
                return
            try:
                queue.put_nowait({
                    "event": event.topic.value,
                    "data": event.payload,
                })
            except asyncio.QueueFull:
                logger.warning(
                    "SSE queue full for tenant %s, dropping event", tenant.id
                )

        # Subscribe to all requested topics
        from tagpulse.events.protocol import Topic

        event_bus = request.app.state.event_bus
        subscribed_topics: list[Topic] = []
        for et in event_types:
            for topic in Topic:
                if topic.value == et:
                    await event_bus.subscribe(topic, _forward)
                    subscribed_topics.append(topic)

        # Record SSE connection
        if hasattr(request.app.state, "usage_meter"):
            request.app.state.usage_meter.record(
                tenant.id, "sse_connections", "connections"
            )

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            # Unsubscribe to prevent handler leak
            for topic in subscribed_topics:
                await event_bus.unsubscribe(topic, _forward)
            logger.info("SSE connection closed for tenant %s", tenant.id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
