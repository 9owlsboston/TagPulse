# Design Document: Observability (Sprint 11)

**Date:** 2026-04-26
**Status:** accepted

---

## 1. Problem Statement

TagPulse lacks runtime visibility into platform performance. Operators cannot answer:
- How many tag reads/sec are we ingesting?
- Which tenants are consuming the most resources?
- Is the EventBus backing up?
- Are rule evaluations keeping up with ingestion?
- Which devices haven't reported recently?
- What happened to a specific tag read as it flowed through ingestion → rules → webhook?

We need metrics and distributed traces exposed for monitoring dashboards and debugging.

---

## 2. Approach: OpenTelemetry

### Why OpenTelemetry over raw Prometheus

| Concern | Prometheus | OpenTelemetry |
|---------|-----------|---------------|
| Metrics | Native | Native |
| Distributed traces | Not supported | Native + auto-instrumentation |
| Log correlation | Separate | Trace ID injected into logs |
| Auto-instrumentation | No | FastAPI, SQLAlchemy, httpx — all free |
| Export targets | Prometheus format only | Prometheus, Jaeger, OTLP, Azure Monitor, Datadog |

OpenTelemetry gives us traces across the full tag read lifecycle (MQTT → ingestion → DB → rule eval → webhook) for the cost of ~30 lines of setup + 5 extra dependencies. Prometheus-format `/metrics` endpoint is preserved via the Prometheus exporter.

### Architecture

```
TagPulse App
  │
  ├── /metrics          → Prometheus format (scraped by Prometheus)
  │
  └── OTLP export       → OTel Collector (optional)
                              ├── Prometheus (metrics)
                              ├── Jaeger (traces)
                              └── Loki (logs)
```

For v1: direct Prometheus scrape + optional Jaeger. No OTel Collector required.

---

## 3. Auto-Instrumented Components

These produce traces automatically with zero code:

| Component | Instrumentation Library | What You Get |
|-----------|------------------------|-------------|
| FastAPI | `opentelemetry-instrumentation-fastapi` | Span per HTTP request (method, path, status, duration) |
| SQLAlchemy | `opentelemetry-instrumentation-sqlalchemy` | Child span per SQL query (query text, duration) |
| httpx | `opentelemetry-instrumentation-httpx` | Child span per outbound HTTP call (webhook delivery) |
| logging | `opentelemetry-instrumentation-logging` | Trace ID injected into log records |

### Example Trace

```
[trace_id: abc123]
├── POST /tag-reads (250ms)
│   ├── INSERT INTO tag_reads (12ms)
│   ├── EventBus.publish TAG_READ_CREATED (1ms)
│   └── RuleEvaluator.on_tag_read (45ms)
│       ├── SELECT rules WHERE tenant_id = ... (8ms)
│       ├── INSERT INTO alerts (5ms)
│       └── WebhookDispatcher._deliver (180ms)
│           └── POST https://example.com/hook (175ms)
```

---

## 4. Custom Metrics (Manual)

In addition to auto-instrumented spans, define custom metrics:

### Platform Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `tagpulse_ingestion_total` | Counter | `tenant_id`, `protocol` | Ingestion service |
| `tagpulse_api_requests_total` | Counter | `method`, `path`, `status_code` | Auto (FastAPI) |
| `tagpulse_api_latency_seconds` | Histogram | `method`, `path` | Auto (FastAPI) |

### EventBus Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `tagpulse_eventbus_published_total` | Counter | `topic` | EventBus.publish() |
| `tagpulse_eventbus_consumed_total` | Counter | `topic` | EventBus._consume() |
| `tagpulse_eventbus_dropped_total` | Counter | `topic` | EventBus overflow |
| `tagpulse_eventbus_queue_size` | UpDownCounter | `topic` | EventBus publish/consume |

### Rule Engine Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `tagpulse_rule_evaluations_total` | Counter | `tenant_id`, `condition_type` | RuleEvaluator |
| `tagpulse_alerts_fired_total` | Counter | `tenant_id`, `severity` | RuleEvaluator |

### Device Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `tagpulse_devices_online` | UpDownCounter | `tenant_id` | Device status updates |

### Integration Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `tagpulse_webhook_deliveries_total` | Counter | `tenant_id`, `status` | WebhookDispatcher |
| `tagpulse_sse_connections_active` | UpDownCounter | `tenant_id` | SSE handler |
| `tagpulse_dead_letters_total` | Counter | `topic` | EventBus dead letter |

---

## 5. Internal Alerting Rules

Platform-level ops alerts (Prometheus alerting rules or log-based):

| Alert | Condition | Severity |
|-------|-----------|----------|
| Ingestion stall | `rate(tagpulse_ingestion_total[5m]) == 0` for active tenant | critical |
| Reader offline | `tagpulse_devices_online == 0` for tenant with registered devices | warning |
| DB lag | `http_server_duration_seconds{quantile="0.99"} > 1` | warning |
| EventBus backup | `tagpulse_eventbus_queue_size > 5000` | warning |
| Webhook failures | `rate(tagpulse_webhook_deliveries_total{status="failed"}[5m]) > 10` | warning |
| Dead letters | `tagpulse_dead_letters_total > 100` | warning |

---

## 6. Implementation

### Setup in main.py

```python
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

def setup_telemetry(app: FastAPI, engine) -> None:
    # Metrics — Prometheus format at /metrics
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))

    # Traces — export to Jaeger/OTel Collector via OTLP (optional)
    tracer_provider = TracerProvider()
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
        )
    trace.set_tracer_provider(tracer_provider)

    # Auto-instrument
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()
    LoggingInstrumentor().instrument()
```

### Custom Metrics Module

```python
# src/tagpulse/core/metrics.py
from opentelemetry import metrics

meter = metrics.get_meter("tagpulse")

ingestion_counter = meter.create_counter(
    "tagpulse_ingestion_total",
    description="Total tag reads ingested",
)

eventbus_published = meter.create_counter(
    "tagpulse_eventbus_published_total",
    description="Events published to EventBus",
)

eventbus_queue_size = meter.create_up_down_counter(
    "tagpulse_eventbus_queue_size",
    description="Current EventBus queue size",
)

# ... other metrics from catalog above
```

### Metrics Endpoint

```python
# src/tagpulse/api/routes/metrics.py
from prometheus_client import make_asgi_app

metrics_app = make_asgi_app()

# Mount at /metrics
app.mount("/metrics", metrics_app)
```

### Project Structure

```
src/tagpulse/
  core/
    telemetry.py        # setup_telemetry() — OTel initialization
    metrics.py          # Custom metric definitions (counters, gauges)
  api/routes/
    metrics.py          # /metrics endpoint (Prometheus format)
```

---

## 7. Dependencies

```
opentelemetry-api>=1.28
opentelemetry-sdk>=1.28
opentelemetry-exporter-prometheus>=0.49
opentelemetry-exporter-otlp-proto-grpc>=1.28
opentelemetry-instrumentation-fastapi>=0.49
opentelemetry-instrumentation-sqlalchemy>=0.49
opentelemetry-instrumentation-httpx>=0.49
opentelemetry-instrumentation-logging>=0.49
```

---

## 8. Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (none) | OTel Collector endpoint for traces. If unset, traces are not exported. |
| `OTEL_SERVICE_NAME` | `tagpulse` | Service name in traces |
| `OTEL_TRACES_SAMPLER` | `always_on` | Sampling strategy (`always_on`, `traceidratio`) |

---

## 9. Docker Compose Addition (optional monitoring stack)

```yaml
  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"    # Jaeger UI
      - "4317:4317"      # OTLP gRPC
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes: ["./docker/prometheus.yml:/etc/prometheus/prometheus.yml"]

  grafana:
    image: grafana/grafana:latest
    ports: ["3001:3000"]
    depends_on: [prometheus, jaeger]
```

### prometheus.yml

```yaml
scrape_configs:
  - job_name: tagpulse
    scrape_interval: 15s
    static_configs:
      - targets: ["app:8000"]
```

---

## 10. Testing Strategy

- Smoke test: `GET /metrics` returns valid Prometheus text format
- Unit test: custom metric increment functions
- No trace assertion tests for v1 (traces are observational, not behavioral)

---

## 11. Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Trace sampling in production? | **Yes** — `traceidratio` at **0.1 (10%)**. Tunable per environment via env var; bump to 1.0 in staging during incident triage. |
| 2 | `/metrics` auth? | **No** — restrict via network policy / `NetworkPolicy` resource. Standard practice for Prometheus scrape endpoints; auth would break scrapers without adding meaningful security inside the cluster. |
