"""Custom application metrics — counters, gauges for TagPulse components."""

from opentelemetry import metrics

meter = metrics.get_meter("tagpulse")

# -- Ingestion --
ingestion_counter = meter.create_counter(
    "tagpulse_ingestion_total",
    description="Total tag reads ingested",
    unit="events",
)

# -- EventBus --
eventbus_published = meter.create_counter(
    "tagpulse_eventbus_published_total",
    description="Events published to EventBus",
    unit="events",
)

eventbus_consumed = meter.create_counter(
    "tagpulse_eventbus_consumed_total",
    description="Events consumed from EventBus",
    unit="events",
)

eventbus_dropped = meter.create_counter(
    "tagpulse_eventbus_dropped_total",
    description="Events dropped due to queue overflow",
    unit="events",
)

eventbus_queue_size = meter.create_up_down_counter(
    "tagpulse_eventbus_queue_size",
    description="Current EventBus queue depth",
    unit="events",
)

# -- Rules --
rule_evaluations = meter.create_counter(
    "tagpulse_rule_evaluations_total",
    description="Rule evaluations performed",
    unit="evaluations",
)

alerts_fired = meter.create_counter(
    "tagpulse_alerts_fired_total",
    description="Alerts triggered by rule evaluations",
    unit="events",
)

# -- Devices --
devices_online = meter.create_up_down_counter(
    "tagpulse_devices_online",
    description="Currently online devices",
    unit="devices",
)

# -- Integrations --
webhook_deliveries = meter.create_counter(
    "tagpulse_webhook_deliveries_total",
    description="Webhook delivery attempts",
    unit="requests",
)

sse_connections = meter.create_up_down_counter(
    "tagpulse_sse_connections_active",
    description="Active SSE streaming connections",
    unit="connections",
)

dead_letters = meter.create_counter(
    "tagpulse_dead_letters_total",
    description="Events sent to dead letter",
    unit="events",
)

# -- Telemetry & location (Sprint 14) --
telemetry_ingestion_counter = meter.create_counter(
    "tagpulse_telemetry_ingestion_total",
    description="Total telemetry readings ingested",
    unit="readings",
)

telemetry_quarantined_counter = meter.create_counter(
    "tagpulse_telemetry_quarantined_total",
    description="Telemetry readings quarantined (unknown / out-of-range / unit mismatch)",
    unit="readings",
)

location_updates_counter = meter.create_counter(
    "tagpulse_location_updates_total",
    description="Standalone location updates ingested",
    unit="updates",
)

device_events_counter = meter.create_counter(
    "tagpulse_device_events_total",
    description="Device-side events ingested",
    unit="events",
)

tag_data_truncations_counter = meter.create_counter(
    "tagpulse_tag_data_truncations_total",
    description="Tag-data JSONB blobs truncated to inline size cap",
    unit="reads",
)

tag_collisions_global_counter = meter.create_counter(
    "tagpulse_tag_collisions_global_total",
    description="Cross-tenant binding_value collisions inspected by admin",
    unit="checks",
)
