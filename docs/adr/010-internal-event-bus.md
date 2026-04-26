# ADR-010: Internal Event Bus Architecture

**Status:** proposed
**Date:** 2026-04-25

## Context

The architecture defines several components that react to events produced by other components:

- **Ingestion** writes tag reads → **Rules engine** must evaluate them
- **Ingestion** writes tag reads → **Analytics modules** must process them
- **Rules engine** triggers alerts → **Integration layer** must deliver them (webhooks, email)
- **Device status** changes → **Integration layer** may need to push notifications

Currently, the architecture is silent on how events flow between these components internally. ADR-004 mentions "background task workers" and ADR-005 says "async background worker," but no event delivery mechanism is defined. Without an explicit internal event bus, each component would need to poll the database or be called directly — coupling producers to consumers and making it difficult to add new subscribers.

## Decision

Introduce an **internal EventBus** with a protocol-based abstraction and a phased implementation strategy.

### EventBus Protocol

```python
from typing import Protocol, Callable, Awaitable
from enum import StrEnum

class Topic(StrEnum):
    TAG_READ_CREATED = "tag_read.created"
    DEVICE_STATUS_CHANGED = "device.status_changed"
    ALERT_TRIGGERED = "alert.triggered"
    DEVICE_REGISTERED = "device.registered"
    DEVICE_DECOMMISSIONED = "device.decommissioned"

EventHandler = Callable[[Event], Awaitable[None]]

class EventBus(Protocol):
    async def publish(self, topic: Topic, event: Event) -> None:
        """Publish an event. Raises EventBusFullError if at capacity."""
        ...

    async def subscribe(self, topic: Topic, handler: EventHandler) -> None:
        """Register a handler for a topic. Multiple handlers per topic allowed."""
        ...

    async def start(self) -> None:
        """Start consuming events (called on app startup)."""
        ...

    async def stop(self, timeout: float = 10.0) -> None:
        """Drain in-flight events and shut down (called on app shutdown)."""
        ...
```

### Capacity Limits and Back-Pressure

Every EventBus implementation enforces capacity limits to prevent unbounded memory growth when consumers fall behind producers.

**Configuration:**

```python
class EventBusSettings(BaseSettings):
    event_bus_capacity: int = 10_000        # max queued events per topic
    event_bus_high_watermark: float = 0.8   # log warning at 80% full
    event_bus_overflow: str = "drop_oldest" # overflow policy: drop_oldest | drop_newest | block | raise
    event_bus_consumer_timeout: float = 30.0  # max seconds for a handler to process one event

    model_config = {"env_prefix": "TAGPULSE_"}
```

**Behavior by overflow policy:**

| Policy | What happens when queue is full | Use case |
|--------|--------------------------------|----------|
| `drop_oldest` | Evict oldest queued event, enqueue new one. Log + increment drop counter. | Telemetry — latest data matters more than old |
| `drop_newest` | Reject the new event. Return immediately. Log + increment drop counter. | When you'd rather lose new events than disrupt in-flight processing |
| `block` | `publish()` awaits until space is available (with timeout). | When no event loss is acceptable — at the cost of slowing ingestion |
| `raise` | `publish()` raises `EventBusFullError`. Caller decides what to do. | Explicit error handling required by the publisher |

**Monitoring:**
- Expose metrics: `event_bus_queue_size` (gauge per topic), `event_bus_events_published` (counter), `event_bus_events_dropped` (counter), `event_bus_consumer_lag` (gauge).
- Log at `WARNING` when queue crosses `high_watermark`.
- Log at `ERROR` on every dropped event with topic and event ID.

### Phase 1 — In-Process AsyncIO EventBus (Sprint 1)

```python
class AsyncEventBus:
    def __init__(self, settings: EventBusSettings) -> None:
        self._queues: dict[Topic, asyncio.Queue[Event]] = {}
        self._handlers: dict[Topic, list[EventHandler]] = {}
        self._settings = settings
        self._tasks: list[asyncio.Task[None]] = []
        self._drop_count: dict[Topic, int] = {}

    async def publish(self, topic: Topic, event: Event) -> None:
        queue = self._queues[topic]
        if queue.full():
            match self._settings.event_bus_overflow:
                case "drop_oldest":
                    try:
                        queue.get_nowait()  # discard oldest
                    except asyncio.QueueEmpty:
                        pass
                    self._drop_count[topic] = self._drop_count.get(topic, 0) + 1
                    logger.warning("EventBus: dropped oldest event on %s (total drops: %d)",
                                   topic, self._drop_count[topic])
                case "drop_newest":
                    self._drop_count[topic] = self._drop_count.get(topic, 0) + 1
                    logger.warning("EventBus: dropped new event on %s (total drops: %d)",
                                   topic, self._drop_count[topic])
                    return
                case "block":
                    await asyncio.wait_for(queue.put(event),
                                           timeout=self._settings.event_bus_consumer_timeout)
                    return
                case "raise":
                    raise EventBusFullError(topic, queue.qsize())
        queue.put_nowait(event)
```

- **Backed by `asyncio.Queue`** with `maxsize=event_bus_capacity`.
- One queue per topic. One consumer task per topic fans out to registered handlers.
- Lives in the same process. No new infrastructure.
- Events are lost if the process crashes — acceptable for MVP.

### Phase 2 — Redis Streams (Sprint 4, with containerization)

```python
class RedisEventBus:
    def __init__(self, redis: Redis, settings: EventBusSettings) -> None: ...
```

- Swap to Redis Streams with consumer groups.
- Capacity enforced via `MAXLEN` on each stream (maps to `event_bus_capacity`).
- Overflow policy: `MAXLEN ~` for approximate trimming (drop oldest), or application-level checks for other policies.
- Durable — events survive process restarts.
- Multiple app instances can form a consumer group (each event processed once).
- Redis is already useful for caching, rate limiting — not a single-purpose dependency.

### Phase 3 — Kafka/Redpanda (scale trigger)

- Swap to Kafka topics with consumer groups.
- Capacity enforced via retention policies and partition-level back-pressure.
- Also serves as the write buffer in front of TimescaleDB (see [storage strategy](../design/storage-strategy.md)).
- Only justified at 100K+ events/sec.

### Event Flow

```
Device ──MQTT/HTTP──→ Ingestion ──write──→ TimescaleDB
                          │
                          └──publish──→ EventBus
                                          │
                         ┌────────────────┼────────────────┐
                         ▼                ▼                ▼
                   Rules Engine     Analytics         (future
                         │          Modules            subscribers)
                         │
                         └──publish──→ EventBus
                                          │
                                          ▼
                                   Integration Layer
                                   (webhooks, email, SSE)
```

### Event Schema

```python
@dataclass(frozen=True)
class Event:
    id: UUID                    # unique event ID
    topic: Topic
    tenant_id: UUID
    timestamp: datetime
    payload: dict[str, Any]     # topic-specific data
```

All events carry `tenant_id` so subscribers can apply tenant-scoped logic without additional lookups.

### Wiring via FastAPI Lifecycle

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    event_bus = AsyncEventBus(settings.event_bus)
    # Register subscribers
    await event_bus.subscribe(Topic.TAG_READ_CREATED, rules_engine.on_tag_read)
    await event_bus.subscribe(Topic.TAG_READ_CREATED, analytics.on_tag_read)
    await event_bus.subscribe(Topic.ALERT_TRIGGERED, integration.on_alert)
    await event_bus.start()
    app.state.event_bus = event_bus
    yield
    await event_bus.stop(timeout=10.0)
```

### Project Structure

```
src/tagpulse/
  events/
    __init__.py
    protocol.py         # EventBus protocol, Event dataclass, Topic enum
    settings.py         # EventBusSettings
    exceptions.py       # EventBusFullError
    async_bus.py        # Phase 1: asyncio.Queue implementation
    # redis_bus.py      # Phase 2: Redis Streams implementation (future)
    # kafka_bus.py      # Phase 3: Kafka implementation (future)
```

## Consequences

- **Good:** Decouples producers from consumers. Ingestion doesn't know about rules, analytics, or integrations.
- **Good:** Adding a new subscriber is one `event_bus.subscribe()` call — no changes to producers.
- **Good:** Capacity limits prevent OOM under load. Configurable overflow policies let operators choose the trade-off.
- **Good:** Protocol abstraction means swapping from in-process to Redis to Kafka requires zero changes to publishers or subscribers.
- **Good:** `tenant_id` on every event enables tenant-scoped processing without additional queries.
- **Bad:** In-process bus (Phase 1) loses events on crash. Acceptable for MVP; mitigated by Phase 2.
- **Bad:** Capacity limits with `drop_oldest`/`drop_newest` mean data loss under sustained overload. Mitigated by monitoring + alerting on drop counters.
- **Bad:** Back-pressure with `block` policy can slow ingestion if consumers are stuck. Mitigated by `consumer_timeout` and dead-letter logging.

## Alternatives Considered

- **PostgreSQL LISTEN/NOTIFY:** No new dependency, cross-process. But not durable (events lost if no listener), 8KB payload limit, no replay, no dead-letter. Insufficient for reliable event delivery.
- **Direct function calls:** Tight coupling — every new consumer requires changes to the producer. No back-pressure or capacity control. Rejected.
- **Celery task queue:** Mature but heavy (requires Redis or RabbitMQ as broker anyway). Task-oriented rather than event-oriented — awkward fit for pub/sub fan-out. Rejected in favor of a lighter EventBus that can use Redis Streams directly.
