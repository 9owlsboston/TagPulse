"""Custom application metrics — counters, gauges for TagPulse components."""

import time

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

tag_reads_without_asset_counter = meter.create_counter(
    "tagpulse_tag_reads_without_asset_total",
    description="Tag reads ingested with no active asset binding",
    unit="reads",
)

subject_zone_changed_counter = meter.create_counter(
    "tagpulse_subject_zone_changed_total",
    description="Asset/stock-item zone-transition events emitted",
    unit="events",
)

external_locations_counter = meter.create_counter(
    "tagpulse_external_locations_recorded_total",
    description="External (non-RFID) location fixes recorded per asset",
    unit="positions",
)

asset_load_counter = meter.create_counter(
    "tagpulse_asset_load_operations_total",
    description="Carrier load/unload operations on the assets containment tree",
    unit="ops",
)

stock_item_auto_created_counter = meter.create_counter(
    "tagpulse_stock_items_auto_created_total",
    description="Stock items auto-created by ingestion on first SGTIN read",
    unit="items",
)

stock_movements_recorded_counter = meter.create_counter(
    "tagpulse_stock_movements_recorded_total",
    description="Stock movement rows appended by ingestion on zone transitions",
    unit="movements",
)

inventory_unmapped_sgtin_counter = meter.create_counter(
    "tagpulse_inventory_unmapped_sgtin_total",
    description="SGTIN reads with no matching product (GTIN lookup miss)",
    unit="reads",
)

# -- Sprint 16: edge contract & identity hardening --
events_rejected_clock_counter = meter.create_counter(
    "tagpulse_events_rejected_clock_total",
    description="Tag-read events rejected by the ingestion clock window "
    "(too old or too far in the future) per docs/design/edge-device-contract.md §3.5",
    unit="events",
)

device_token_rotations_counter = meter.create_counter(
    "tagpulse_device_token_rotations_total",
    description="Per-device Bearer token rotations (ADR-011 Phase 1)",
    unit="rotations",
)

device_cert_attachments_counter = meter.create_counter(
    "tagpulse_device_cert_attachments_total",
    description=(
        "Device-certificate attachments via POST /device-registry/{id}/cert "
        "(ADR-012 Phase 2 mTLS for MQTT)."
    ),
    unit="attachments",
)

# -- Sprint 17a: geofencing & map --
geofence_evaluation_duration = meter.create_histogram(
    "geofence_evaluation_duration",
    description=(
        "Per-evaluation latency for the in-process geofence point-in-polygon "
        "test. p99 > 10ms sustained 1h opens ADR-013 (PostGIS adoption) per "
        "docs/design/geofencing-and-map.md §11 Q5."
    ),
    unit="s",
)

geofence_candidates_per_evaluation = meter.create_histogram(
    "geofence_candidates_per_evaluation",
    description=(
        "Polygons surviving the SQL bbox prefilter per ingest. p95 > 50 "
        "sustained 1h opens ADR-013 (PostGIS adoption) per "
        "docs/design/geofencing-and-map.md §11 Q5."
    ),
    unit="1",
)

geofence_transitions_counter = meter.create_counter(
    "tagpulse_geofence_transitions_total",
    description="Geofence-zone enter/exit transitions emitted by ingestion.",
    unit="events",
)

dwell_evaluations_counter = meter.create_counter(
    "tagpulse_dwell_evaluations_total",
    description="Dwell-worker scans of asset_current_zone (Sprint 17a §5.2).",
    unit="scans",
)

dwell_alerts_counter = meter.create_counter(
    "tagpulse_dwell_alerts_total",
    description="Synthetic zone.dwell_exceeded alerts emitted by the dwell worker.",
    unit="alerts",
)

# -- Sprint 28 C1: MQTT subscriber operational metrics --

mqtt_reconnect_attempts_counter = meter.create_counter(
    "tagpulse_mqtt_reconnect_attempts_total",
    description=(
        "MQTT subscriber connection / reconnection attempts. The 'reason' "
        "label classifies the trigger: 'startup' for the first attempt, "
        "or a short error class (e.g. 'connection_refused', 'auth_failed', "
        "'timeout', 'other') for retries. Worker stays connected on the "
        "happy path so this rate should be near zero."
    ),
    unit="attempts",
)

mqtt_messages_rejected_counter = meter.create_counter(
    "tagpulse_mqtt_messages_rejected_total",
    description=(
        "Inbound MQTT messages dropped by the subscriber before reaching "
        "ingestion. Labelled by 'topic_kind' (tag_read | status | telemetry "
        "| location | event | subject_telemetry | unparseable | unknown_suffix) "
        "and 'reason' (invalid_json | invalid_schema | non_dict_payload | "
        "no_valid_items | invalid_topic | unknown_subject_kind | unknown_suffix)."
    ),
    unit="messages",
)


# Tracks the wall-clock time the subscriber last processed any message.
# Mutable container so the observable-gauge callback can read it without a
# closure over a frame-local. Sentinel 0.0 = never received.
_MQTT_LAST_MESSAGE_TS: dict[str, float] = {"value": 0.0}


def mark_mqtt_message_processed() -> None:
    """Mark "now" as the last time the MQTT subscriber processed a message.

    Called from the subscriber's message loop after a successful (or
    deliberately-dropped) handler return so the
    ``mqtt_subscriber_last_message_age_seconds`` gauge reflects liveness.
    """
    _MQTT_LAST_MESSAGE_TS["value"] = time.time()


def mqtt_message_age_seconds() -> float | None:
    """Return seconds since the MQTT subscriber last processed a message.

    Returns ``None`` when no message has ever been processed (sentinel
    0.0). Used by the Sprint 28 D4 ``/health/detail`` exposure so cloud
    operators can see ingest freshness without scraping OTLP.
    """
    last = _MQTT_LAST_MESSAGE_TS["value"]
    if last <= 0.0:
        return None
    return time.time() - last


def _observe_mqtt_age(_options):  # type: ignore[no-untyped-def]
    last = _MQTT_LAST_MESSAGE_TS["value"]
    if last <= 0.0:
        # Never received a message yet — emit nothing rather than report
        # a misleading "age = process uptime".
        return []
    return [metrics.Observation(time.time() - last)]


mqtt_subscriber_last_message_age_seconds = meter.create_observable_gauge(
    name="tagpulse_mqtt_subscriber_last_message_age_seconds",
    callbacks=[_observe_mqtt_age],
    description=(
        "Seconds since the MQTT subscriber last processed a message of any "
        "kind. A monotonically rising value while devices are publishing "
        "indicates a stalled subscriber (broker still up, but our consumer "
        "has stopped reading). See Sprint 28 D4 healthz exposure."
    ),
    unit="s",
)


# -- Sprint 46 Phase E: v2 wire-format + presence reconciler counters --
# All per spec §6 (docs/design/edge-wire-format-v2.md) + roadmap Phase E
# (docs/roadmap.md). Counter wiring is best-effort — call sites swallow
# OTel exceptions so an instrumentation failure cannot stall the MQTT
# message loop or the reconciler transaction.

mqtt_wm_rejections_counter = meter.create_counter(
    "tagpulse_mqtt_wm_rejections_total",
    description=(
        "v2 wire-format MQTT messages rejected before reaching the "
        "presence reconciler. The 'reason' label is one of the spec §6 "
        "rows (missing_type, unknown_type, invalid_epc, "
        "missing_required_field, epcs_wrong_type, invalid_snap_entry, "
        "explicit_null, invalid_json, invalid_schema). Rejections are "
        "also persisted to the DLQ via _persist_mqtt_drop (Sprint 28 C3)."
    ),
    unit="messages",
)

mqtt_wm_snap_large_counter = meter.create_counter(
    "tagpulse_mqtt_wm_snap_large_total",
    description=(
        "v2 snapshot messages whose 'epcs[]' length exceeds the §6 soft "
        "cap (default 5000). Soft cap is warning-only: messages are "
        "processed in full, not rejected. The 'sn' label identifies the "
        "producer's per-device serial; cardinality is bounded by device "
        "count."
    ),
    unit="messages",
)

mqtt_wm_sub_no_presence_counter = meter.create_counter(
    "tagpulse_mqtt_wm_sub_no_presence_total",
    description=(
        "v2 t=2 (disappeared) deltas for an EPC the server has never "
        "seen in tag_presence for the (tenant, device) pair. Logged at "
        "debug + counted, not rejected (spec §6). A sustained non-zero "
        "rate indicates either subscriber state loss (pod restart with "
        "no snap-on-reconnect yet) or a producer with no snap cadence."
    ),
    unit="messages",
)

presence_reconcile_duration_seconds = meter.create_histogram(
    "tagpulse_presence_reconcile_duration_seconds",
    description=(
        "Wall-clock seconds spent applying one v2 message (snap, "
        "appeared, or disappeared) to tag_presence inside the "
        "subscriber's transaction. Labelled by 't' "
        "(snap | appeared | disappeared)."
    ),
    unit="s",
)

presence_entries_counter = meter.create_counter(
    "tagpulse_presence_entries_total",
    description=(
        "tag_presence row writes by resulting status. Incremented per "
        "upsert: 'present' on every snap entry and every appeared "
        "delta; 'gone' on each present→gone transition (either via a "
        "snap that omits the EPC or via an explicit t=2). Useful for "
        "presence-table throughput dashboards independent of event "
        "emission."
    ),
    unit="rows",
)

signaling_tag_appeared_counter = meter.create_counter(
    "tagpulse_signaling_tag_appeared_total",
    description=(
        "SIGNALING_TAG_APPEARED events emitted by the presence "
        "reconciler. Counts transitions to 'present' (new EPC or "
        "gone→present). Labelled by 'source' (snap | delta) so an "
        "operator can tell snap-on-reconnect bursts from steady-state "
        "delta-driven appearances."
    ),
    unit="events",
)

signaling_tag_disappeared_counter = meter.create_counter(
    "tagpulse_signaling_tag_disappeared_total",
    description=(
        "SIGNALING_TAG_DISAPPEARED events emitted by the presence "
        "reconciler. Counts present→gone transitions. Labelled by "
        "'source' (snap | delta) so an operator can tell §5.7 "
        "snap-driven self-heals from prompt t=2 deltas."
    ),
    unit="events",
)


# -- Labels (Sprint 35, ADR 020) --
labels_associations_total = meter.create_counter(
    "tagpulse_labels_associations_total",
    description=(
        "Label-to-entity associations created via POST "
        "/{entity_type}/{entity_id}/labels. Attributes: tenant_id, "
        "entity_type. Use the counter to spot tenants approaching the "
        "30-per-entity cap or to track tag-driven inventory growth."
    ),
    unit="associations",
)
