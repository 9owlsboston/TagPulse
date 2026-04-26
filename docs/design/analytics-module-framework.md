# Design Document: Analytics Module Framework (Sprint 7)

**Date:** 2026-04-25
**Status:** proposed
**Related:** [ADR-004 (Monolith-first with plugin analytics)](../adr/004-monolith-plugin-analytics.md)

---

## 1. Problem Statement

TagPulse needs extensible analytics that:
- Process tag read telemetry in the background without blocking ingestion
- Follow a plugin pattern so new modules can be added mechanically
- Share the DB connection pool and EventBus (per ADR-004)
- Are tenant-scoped (multi-tenancy from Sprint 5)
- Store computed results for query via API

The first module: **read frequency analytics** — reads/min per reader, anomaly flagging.

---

## 2. Plugin Interface

### Base Class

```python
class AnalyticsModule(ABC):
    """Base class all analytics modules must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique module name (e.g. 'read_frequency')."""

    @property
    @abstractmethod
    def subscribed_topics(self) -> list[Topic]:
        """EventBus topics this module listens to."""

    @abstractmethod
    async def on_event(self, event: Event) -> None:
        """Process a single event. Called by the background worker."""

    async def start(self) -> None:
        """Optional lifecycle hook — called once on app startup."""

    async def stop(self) -> None:
        """Optional lifecycle hook — called on app shutdown."""
```

### Why ABC, not Protocol

Modules need `start()`/`stop()` default implementations and registration metadata (`name`, `subscribed_topics`). ABC provides this; Protocol doesn't support defaults or `@abstractmethod`.

---

## 3. Module Registration

Explicit registry in the app lifespan — no dynamic discovery. Simple list:

```python
# In main.py lifespan:
modules: list[AnalyticsModule] = [
    ReadFrequencyModule(session_factory=async_session_factory),
]
for module in modules:
    await module.start()
    for topic in module.subscribed_topics:
        await event_bus.subscribe(topic, module.on_event)
```

Adding a new module = one line in the list + the module class. No magic.

---

## 4. Background Worker Pattern

Analytics modules subscribe to EventBus topics (same as the rule evaluator). The EventBus already runs consumer tasks in the background (`AsyncEventBus._consume()`). No separate worker infrastructure needed for v1.

If a module needs periodic computation (not event-driven), it uses an `asyncio.Task` started in `start()`:

```python
class ReadFrequencyModule(AnalyticsModule):
    async def start(self) -> None:
        self._task = asyncio.create_task(self._compute_loop())

    async def _compute_loop(self) -> None:
        while True:
            await asyncio.sleep(60)  # compute every minute
            await self._compute_frequencies()
```

---

## 5. Data Access

Modules receive a `session_factory` at construction (same pattern as MQTT subscriber). Each event handler or compute cycle creates a scoped session:

```python
async def on_event(self, event: Event) -> None:
    async with self._session_factory() as session:
        # query + write within scoped session
        await session.commit()
```

This avoids the long-lived session problem (P1 from Sprint 2 audit).

---

## 6. First Module: Read Frequency Analytics

### What It Computes

| Metric | Description | Storage |
|--------|-------------|---------|
| `reads_per_minute` | Rolling count of tag reads per device per minute | `analytics_results` table |
| `anomaly_flag` | True if reads/min deviates > 2 standard deviations from 1-hour rolling average | Same table |

### How It Works

1. **Event-driven**: Subscribes to `TAG_READ_CREATED`.
2. **On each event**: Increments an in-memory counter `{(tenant_id, device_id): count}`.
3. **Every 60 seconds**: Flushes counters to `analytics_results` table, computes anomaly flags by comparing current rate vs. recent average from DB.
4. **API**: Results queryable via `GET /analytics/read-frequency?device_id=X`.

### Data Model

```
analytics_results
-----------------
id              UUID PK
tenant_id       UUID FK → tenants.id
module_name     VARCHAR(100) NOT NULL        -- 'read_frequency'
device_id       UUID NOT NULL
metric_name     VARCHAR(100) NOT NULL        -- 'reads_per_minute', 'anomaly_flag'
metric_value    FLOAT NOT NULL
computed_at     TIMESTAMPTZ NOT NULL (index)
```

This is a generic table — future modules store results here too, keyed by `module_name` + `metric_name`.

### Anomaly Detection (Simple)

```python
mean = average reads/min over last 60 minutes for this device
stddev = standard deviation over same window
anomaly = current_rate > mean + 2 * stddev or current_rate < mean - 2 * stddev
```

No ML. Simple statistical threshold. Sufficient for v1.

---

## 7. Tenant Scoping

- `analytics_results` has `tenant_id` FK.
- Modules receive `tenant_id` from the event payload (already present since Sprint 5 fix).
- API endpoint filters by tenant via `get_current_tenant`.

---

## 8. Project Structure

```
src/tagpulse/analytics/
    __init__.py              # AnalyticsModule base class + registry
    read_frequency.py        # First module implementation
src/tagpulse/api/routes/
    analytics.py             # GET /analytics/read-frequency
migrations/versions/
    008_analytics_results.py # analytics_results table
```

---

## 9. Testing Strategy

- Unit tests: `ReadFrequencyModule.on_event()` with fake session
- Unit tests: anomaly detection logic (pure math)
- Unit tests: counter flush logic
- No integration tests needed for v1 (same pattern as rules evaluator)

---

## 10. Open Questions

- Should anomaly thresholds be configurable per device/tenant? (Defer — hardcode 2σ for v1)
- Should analytics results be a hypertable? (Yes if retention policies needed; defer decision)
