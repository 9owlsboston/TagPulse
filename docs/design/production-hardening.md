# Design Document: Production Hardening (Sprint 10)

**Date:** 2026-04-25
**Status:** proposed
**Related:** [ADR-009 (Containerization)](../adr/009-containerization-local-dev.md)

---

## 1. Problem Statement

TagPulse runs in development mode with minimal resilience. For production deployment we need:

- Deep health checks (not just `{"status": "ok"}`)
- Graceful shutdown (drain connections, flush buffers)
- Failed message handling (retry + dead letter)
- Production-ready container configuration
- Operational runbooks

---

## 2. Deep Health Checks

### Current State

`GET /health` returns `{"status": "ok"}` — no dependency checking.

### Target

```
GET /health          → liveness probe (fast, no deps)
GET /health/ready    → readiness probe (checks all deps)
GET /health/detail   → detailed status per component (admin only)
```

### Readiness Checks

| Component | Check | Timeout |
|-----------|-------|---------|
| Database | `SELECT 1` via async session | 3s |
| MQTT Broker | TCP connect to broker host:port | 3s |
| EventBus | `self._running` flag + queue sizes | instant |
| UsageMeter | `self._task` not None and not done | instant |

### Response Schema

```json
{
  "status": "healthy",
  "checks": {
    "database": {"status": "up", "latency_ms": 2},
    "mqtt": {"status": "up", "latency_ms": 5},
    "event_bus": {"status": "up", "queue_sizes": {"tag_read.created": 0}},
    "usage_meter": {"status": "up"}
  }
}
```

If any check fails: `status = "degraded"`, HTTP 503 on `/health/ready`.

### Implementation

```python
# src/tagpulse/api/routes/health.py
@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    checks = await run_all_checks(request.app)
    status = "healthy" if all_up(checks) else "degraded"
    code = 200 if status == "healthy" else 503
    return JSONResponse({"status": status, "checks": checks}, status_code=code)
```

---

## 3. Graceful Shutdown

### Current State

Lifespan `yield` cancels MQTT task + stops EventBus and UsageMeter. But:
- No drain period — in-flight requests are dropped
- MQTT messages being processed may lose data
- EventBus queues may have unprocessed events

### Target Shutdown Sequence

```
1. Signal received (SIGTERM)
2. Stop accepting new HTTP requests (uvicorn handles this)
3. Stop MQTT subscriber (cancel task, await completion)
4. Drain EventBus queues (process remaining events, timeout 10s)
5. Flush UsageMeter (final flush to DB)
6. Flush analytics modules (final compute cycle)
7. Stop webhook dispatcher (close HTTP client)
8. Stop alert delivery service
9. Close DB connection pool
10. Exit
```

### Implementation

The current lifespan already handles steps 3-8. Add:

```python
# In lifespan shutdown:
# Drain EventBus — process remaining events before stopping
await event_bus.drain(timeout=10.0)  # New method: process queued events, then stop
```

### `AsyncEventBus.drain()`

```python
async def drain(self, timeout: float = 10.0) -> None:
    """Process remaining queued events, then stop."""
    self._running = False  # Stop accepting new events
    deadline = asyncio.get_event_loop().time() + timeout
    for topic, queue in self._queues.items():
        while not queue.empty() and asyncio.get_event_loop().time() < deadline:
            event = queue.get_nowait()
            for handler in self._handlers.get(topic, []):
                await handler(event)
    await self.stop(timeout=1.0)
```

---

## 4. Retry + Dead Letter for Failed Messages

### Current State

- MQTT messages that fail ingestion → logged, dropped
- Webhook failures → retried inline (Sprint 8 fix), then dead-lettered
- EventBus events that fail handler → logged, dropped

### Target

Add a `dead_letter_events` table for events that failed all processing:

```
dead_letter_events
------------------
id              UUID PK
tenant_id       UUID NULL
topic           VARCHAR(50) NOT NULL
payload         JSONB NOT NULL
error_message   TEXT NOT NULL
failed_at       TIMESTAMPTZ NOT NULL
retry_count     INT NOT NULL DEFAULT 0
status          VARCHAR(20) NOT NULL DEFAULT 'pending'  -- pending | retried | abandoned
```

### EventBus Dead Letter Handler

When a handler raises an exception in `_consume()`, after logging, persist to dead letter table:

```python
except Exception:
    logger.exception(...)
    await self._dead_letter(topic, event, traceback.format_exc())
```

### MQTT Ingestion Dead Letter

When `_handle_tag_read()` fails, persist the raw message:

```python
except Exception:
    logger.exception(...)
    await self._dead_letter_mqtt(message)
```

### Dead Letter API

```
GET /admin/dead-letter              → list dead-lettered events
POST /admin/dead-letter/{id}/retry  → re-publish event to EventBus
DELETE /admin/dead-letter/{id}      → abandon (mark as abandoned)
```

---

## 5. Production Dockerfile

### Current State

Dockerfile exists (Sprint 4) with multi-stage build and non-root user.

### Enhancements

```dockerfile
# Add health check instruction
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Add labels
LABEL org.opencontainers.image.source="https://github.com/9owlsboston/TagPulse"
LABEL org.opencontainers.image.version="0.1.0"

# Production CMD (no --reload)
CMD ["uvicorn", "tagpulse.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--log-level", "info"]
```

### Production docker-compose.prod.yml

```yaml
services:
  app:
    image: tagpulse:latest
    deploy:
      replicas: 2
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
    environment:
      DATABASE_URL: ${DATABASE_URL}
      MQTT_BROKER_HOST: ${MQTT_BROKER_HOST}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

---

## 6. Runbooks

### Runbook: Service Won't Start
1. Check logs: `docker compose logs app`
2. Verify DB connectivity: `docker compose exec db pg_isready`
3. Verify MQTT broker: `docker compose exec mqtt mosquitto_sub -t '#' -C 1`
4. Check migrations: `docker compose exec app alembic current`
5. Run migrations: `docker compose exec app alembic upgrade head`

### Runbook: High Ingestion Latency
1. Check EventBus queue sizes: `GET /health/detail`
2. Check DB connection pool: look for "pool exhausted" in logs
3. Check MQTT subscriber: look for "subscriber crashed" in logs
4. Scale workers: increase `--workers` in Dockerfile CMD

### Runbook: Dead-Lettered Events
1. List: `GET /admin/dead-letter`
2. Inspect payload and error_message
3. Fix root cause (usually downstream service down)
4. Retry: `POST /admin/dead-letter/{id}/retry`
5. Monitor: check if retry succeeds

### Runbook: Tenant Quota Exceeded
1. Check usage: `GET /admin/usage?start=today`
2. Check quotas: query `tenant_quotas` table
3. Increase quota or contact tenant
4. Clear throttle: events resume automatically after quota period resets

---

## 7. Project Structure (new files)

```
src/tagpulse/
  api/routes/
    health.py           # Liveness + readiness + detail endpoints
  core/
    health_checks.py    # DB, MQTT, EventBus check implementations
migrations/versions/
  011_dead_letter.py    # dead_letter_events table
docs/
  runbooks/
    service-start.md
    high-latency.md
    dead-letter.md
    quota-exceeded.md
```

---

## 8. Testing Strategy

- Unit tests: each health check component (mock DB, mock MQTT)
- Unit tests: EventBus.drain() — verify queued events processed
- Unit tests: dead letter persistence
- Smoke test: Docker build + health check passes

---

## 9. Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Auto-retry dead letters on a schedule? | **No, manual via API for v1.** Auto-retry hides systemic problems; operators should be in the loop. Revisit when DLQ volume warrants automation. |
| 2 | Prometheus metrics endpoint timing? | **Sprint 11** (observability sprint) — keeps S10 focused on hardening basics. |
| 3 | Uvicorn worker count? | **2 for v1**; auto-scale based on CPU cores later via container orchestrator settings. |
