"""FastAPI application entry point."""

import asyncio
import contextlib
import logging
import uuid as uuid_mod
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from tagpulse.analytics import AnalyticsModule
from tagpulse.analytics.read_frequency import ReadFrequencyModule
from tagpulse.api.routes.admin import router as admin_router
from tagpulse.api.routes.admin_ops import router as admin_ops_router
from tagpulse.api.routes.analytics import router as analytics_router
from tagpulse.api.routes.antennas import router as antennas_router
from tagpulse.api.routes.assets import router as assets_router
from tagpulse.api.routes.auth import router as auth_router
from tagpulse.api.routes.bulk_operations import router as bulk_operations_router
from tagpulse.api.routes.categories import router as categories_router
from tagpulse.api.routes.dashboard import router as dashboard_router
from tagpulse.api.routes.devices import router as devices_router
from tagpulse.api.routes.health import router as health_router
from tagpulse.api.routes.ingestion import router as ingestion_router
from tagpulse.api.routes.integrations import router as integrations_router
from tagpulse.api.routes.inventory import router as inventory_router
from tagpulse.api.routes.inventory_imports import router as inventory_imports_router
from tagpulse.api.routes.labels import router as labels_router
from tagpulse.api.routes.metrics import router as metrics_router
from tagpulse.api.routes.provisioning import router as provisioning_router
from tagpulse.api.routes.query import router as query_router
from tagpulse.api.routes.rules import router as rules_router
from tagpulse.api.routes.security import router as security_router
from tagpulse.api.routes.sites_zones import router as sites_zones_router
from tagpulse.api.routes.tags import router as tags_router
from tagpulse.api.routes.telemetry import router as telemetry_router
from tagpulse.api.routes.telemetry_models import router as telemetry_models_router
from tagpulse.api.routes.tenant_branding import router as tenant_branding_router
from tagpulse.api.routes.tenant_config import router as tenant_config_router
from tagpulse.api.routes.ui_config import router as ui_config_router
from tagpulse.api.routes.users import router as users_router
from tagpulse.core.config import settings
from tagpulse.core.logging import setup_logging
from tagpulse.core.migration_check import (
    MigrationVersionMismatch,
    assert_migration_head,
)
from tagpulse.core.rate_limit import rate_limit_middleware
from tagpulse.core.telemetry import setup_telemetry
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.async_bus import AsyncEventBus
from tagpulse.events.protocol import Topic
from tagpulse.ingestion.mqtt_subscriber import MqttSubscriber
from tagpulse.integrations.sse import router as sse_router
from tagpulse.integrations.webhook import WebhookDispatcher
from tagpulse.repositories.timescaledb.session import async_session_factory
from tagpulse.rules.delivery import AlertDeliveryService
from tagpulse.rules.evaluator import RuleEvaluator
from tagpulse.signaling.periodic_dispatcher import PeriodicSignalingDispatcher
from tagpulse.workers.consolidation_worker import AssetConsolidationWorker
from tagpulse.workers.dwell_worker import DwellTracker, DwellWorker
from tagpulse.workers.floor_position_worker import FloorPositionWorker
from tagpulse.workers.inventory_rule_worker import InventoryRuleWorker
from tagpulse.workers.leg_tracker import AssetLegTracker
from tagpulse.workers.tag_registrar_worker import TagRegistrarWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle — start/stop EventBus and MQTT subscriber."""
    setup_logging(settings.log_level)
    setup_telemetry(app)

    # Sprint 22 A7: refuse to boot when DB schema doesn't match the
    # code's alembic head. Always-on in staging/production (forced by
    # the Settings validator); opt-in in dev so an in-flight migration
    # branch doesn't block ``make run``.
    if settings.strict_migration_check:
        try:
            await assert_migration_head(async_session_factory)
        except MigrationVersionMismatch:
            logger.exception("strict_migration_check failed; aborting startup")
            raise

    # Sprint 22 A3: warn loudly when geofence evaluation is off in a
    # non-dev environment. Operators can still flip the flag at runtime;
    # this is just a "did you mean to leave this off?" guardrail.
    if settings.environment != "dev" and not settings.geofence_evaluation_enabled:
        logger.warning(
            "geofence_evaluation_enabled is False in environment=%s; "
            "zone.entered/exited/dwell rules will not fire. "
            "Set GEOFENCE_EVALUATION_ENABLED=true to enable.",
            settings.environment,
        )

    # EventBus — create but don't start yet (start after all subscriptions)
    event_bus = AsyncEventBus(
        capacity=settings.event_bus_capacity,
        dead_letter_factory=async_session_factory,
    )
    app.state.event_bus = event_bus

    # UsageMeter
    usage_meter = UsageMeter(session_factory=async_session_factory)
    await usage_meter.start()
    app.state.usage_meter = usage_meter

    # Sprint 22 B1: worker components run in this process only when
    # ``workers_inline`` is True. In cloud deployments the API container
    # sets ``WORKERS_INLINE=false`` and a sibling worker container
    # (same image, default ``WORKERS_INLINE=true``) hosts these. The
    # event_bus + usage_meter above are unconditional because HTTP
    # routes / SSE / dependencies read them from ``app.state``.
    inventory_worker: InventoryRuleWorker | None = None
    dwell_worker: DwellWorker | None = None
    dwell_tracker: DwellTracker | None = None
    tag_registrar_worker: TagRegistrarWorker | None = None
    periodic_signaling_dispatcher: PeriodicSignalingDispatcher | None = None
    floor_position_worker: FloorPositionWorker | None = None
    asset_consolidation_worker: AssetConsolidationWorker | None = None
    alert_delivery: AlertDeliveryService | None = None
    analytics_modules: list[AnalyticsModule] = []
    webhook_dispatcher: WebhookDispatcher | None = None
    mqtt_task: asyncio.Task[None] | None = None

    if settings.workers_inline:
        # Rule evaluator — subscribes to tag read events
        evaluator = RuleEvaluator(
            session_factory=async_session_factory,
            event_bus=event_bus,
            usage_meter=usage_meter,
        )
        await event_bus.subscribe(Topic.TAG_READ_CREATED, evaluator.on_tag_read)
        await event_bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, evaluator.on_subject_zone_changed)
        # Sprint 20: subject-scoped telemetry threshold rules.
        await event_bus.subscribe(Topic.TELEMETRY_RECORDED, evaluator.on_telemetry_recorded)
        # Sprint 41 Phase D: on_inference rules consuming attribution-settled
        # events from the OverlappingZones processor.
        await event_bus.subscribe(
            Topic.SIGNALING_ATTRIBUTION_SETTLED, evaluator.on_attribution_settled
        )

        # Inventory rule worker — periodic scans for stock.below_threshold and
        # stock.expiring_within rules + daily stock_items_active metering snapshot.
        inventory_worker = InventoryRuleWorker(
            session_factory=async_session_factory,
            event_bus=event_bus,
            usage_meter=usage_meter,
        )
        await inventory_worker.start()
        app.state.inventory_worker = inventory_worker

        # Sprint 17a: dwell tracker + worker for zone.dwell_exceeded rules.
        # Tracker is write-through to subject_current_zone (migration 027) so
        # dwell state survives restart and is shared across workers.
        dwell_tracker = DwellTracker(session_factory=async_session_factory)
        await dwell_tracker.hydrate()
        await event_bus.subscribe(Topic.SUBJECT_ZONE_CHANGED, dwell_tracker.on_subject_zone_changed)
        dwell_worker = DwellWorker(
            session_factory=async_session_factory,
            event_bus=event_bus,
            usage_meter=usage_meter,
            tracker=dwell_tracker,
            interval_s=float(settings.dwell_worker_interval_s),
        )
        await dwell_worker.start()
        app.state.dwell_tracker = dwell_tracker
        app.state.dwell_worker = dwell_worker

        # Sprint 50 Phase D (ADR 028): tag registrar worker drains
        # ``tag_reads.tag_known IS NULL``, populates the three-valued
        # gating column, and promotes ``registered → active`` on first
        # observed read. Ingest hot path stays free of any ``tags`` reads.
        tag_registrar_worker = TagRegistrarWorker(
            session_factory=async_session_factory,
            interval_s=settings.tag_registrar_interval_s,
            batch_size=settings.tag_registrar_batch_size,
        )
        await tag_registrar_worker.start()
        app.state.tag_registrar_worker = tag_registrar_worker

        # Sprint 41 Phase B3 / ADR-021 v2: PeriodicSignalingDispatcher.
        # Wakes on its loop tick and evaluates ``signaling.*.periodic``
        # rules whose configured ``cadence_minutes`` has elapsed.
        # Phase B ships the dispatcher shell; per-event-type processor
        # logic lands in Phase D.
        periodic_signaling_dispatcher = PeriodicSignalingDispatcher(
            session_factory=async_session_factory,
            event_bus=event_bus,
            usage_meter=usage_meter,
        )
        await periodic_signaling_dispatcher.start()
        app.state.periodic_signaling_dispatcher = periodic_signaling_dispatcher

        # Sprint 66 (Phase 2): floor-position estimator worker (Option-C tick).
        # Gated off by default — flip ``position_estimator_enabled`` only after
        # the DB adapters are integration-validated. Writes
        # ``asset_positions(source='computed')`` from reader RSSI.
        app.state.floor_position_worker = None
        if settings.position_estimator_enabled:
            from tagpulse.repositories.timescaledb.floor_position_source import (
                TimescaleObservationSource,
                TimescalePositionWriter,
                TimescaleStrategySource,
            )
            from tagpulse.services.floor_position_estimator import (
                FloorPositionEstimatorService,
            )

            floor_position_worker = FloorPositionWorker(
                FloorPositionEstimatorService(
                    observations=TimescaleObservationSource(),
                    writer=TimescalePositionWriter(),
                    strategies=TimescaleStrategySource(async_session_factory),
                ),
                interval_s=float(settings.position_estimator_interval_s),
            )
            await floor_position_worker.start()
            app.state.floor_position_worker = floor_position_worker

        # Sprint 71 (ADR-034): asset-state consolidation worker. Gated off by
        # default — flip ``consolidation_enabled`` only after the DB adapters
        # are integration-validated. Fuses each asset's bound-tag reads into one
        # ``asset_state_history`` snapshot (zone vote + environment mean) and
        # emits ``ASSET_CUSTODY_CHANGED`` on frame transitions.
        app.state.asset_consolidation_worker = None
        if settings.consolidation_enabled:
            asset_consolidation_worker = AssetConsolidationWorker(
                async_session_factory,
                event_bus=event_bus,
                interval_s=float(settings.consolidation_interval_s),
            )
            await asset_consolidation_worker.start()
            app.state.asset_consolidation_worker = asset_consolidation_worker

            # Sprint 72 (ADR-034 Phase 2): leg tracker subscribes to the
            # custody events the consolidation worker emits and opens/closes
            # ``asset_legs`` (transit legs + cold-chain SLA). Stateless — the
            # table holds the open-leg state, so nothing to hydrate.
            asset_leg_tracker = AssetLegTracker(async_session_factory)
            await event_bus.subscribe(
                Topic.ASSET_CUSTODY_CHANGED, asset_leg_tracker.on_custody_changed
            )
            app.state.asset_leg_tracker = asset_leg_tracker

        # Alert delivery — subscribes to alert triggered events
        alert_delivery = AlertDeliveryService()
        await alert_delivery.start()
        await event_bus.subscribe(Topic.ALERT_TRIGGERED, alert_delivery.on_alert_triggered)
        app.state.alert_delivery = alert_delivery

        # Analytics modules
        analytics_modules = [
            ReadFrequencyModule(session_factory=async_session_factory),
        ]
        for module in analytics_modules:
            await module.start()
            for topic in module.subscribed_topics:
                await event_bus.subscribe(topic, module.on_event)
        app.state.analytics_modules = analytics_modules

        # Webhook dispatcher — subscribes to all events for webhook delivery
        webhook_dispatcher = WebhookDispatcher(
            session_factory=async_session_factory, usage_meter=usage_meter
        )
        await webhook_dispatcher.start()
        for topic in Topic:
            await event_bus.subscribe(topic, webhook_dispatcher.on_event)
        app.state.webhook_dispatcher = webhook_dispatcher
    else:
        logger.info(
            "workers_inline=False; this process serves HTTP only. "
            "Run a sibling container with WORKERS_INLINE=true to host "
            "MQTT subscriber + inventory/dwell/alert/analytics/webhook workers."
        )

    # Start EventBus AFTER all subscribers are registered
    await event_bus.start()

    # MQTT subscriber background task (worker process only)
    if settings.workers_inline and settings.mqtt_broker_host:
        mqtt_task = asyncio.create_task(_run_mqtt_subscriber(event_bus, usage_meter))
        logger.info("MQTT subscriber task started")

    yield

    # Shutdown
    if mqtt_task is not None:
        mqtt_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mqtt_task
    if dwell_worker is not None:
        await dwell_worker.stop()
    if inventory_worker is not None:
        await inventory_worker.stop()
    if tag_registrar_worker is not None:
        await tag_registrar_worker.stop()
    if periodic_signaling_dispatcher is not None:
        await periodic_signaling_dispatcher.stop()
    if floor_position_worker is not None:
        await floor_position_worker.stop()
    if asset_consolidation_worker is not None:
        await asset_consolidation_worker.stop()
    await usage_meter.stop()
    for module in analytics_modules:
        await module.stop()
    if webhook_dispatcher is not None:
        await webhook_dispatcher.stop()
    if alert_delivery is not None:
        await alert_delivery.stop()
    await event_bus.drain(timeout=10.0)


async def _run_mqtt_subscriber(event_bus: AsyncEventBus, usage_meter: UsageMeter) -> None:
    """Run MQTT subscriber with per-message sessions to avoid stale ORM state.

    Sprint 31 (#18): supervised. If ``subscriber.run()`` ever returns or
    raises (broker disconnect, unhandled crash inside the loop, etc.) we
    log + sleep with exponential backoff and reconnect, so a transient
    failure or a future regression cannot take ingest fully offline
    while the rest of the worker keeps reporting healthy. Cancellation
    (graceful shutdown) still exits immediately.
    """
    backoff_s = 1.0
    backoff_max_s = 60.0
    while True:
        subscriber = MqttSubscriber(
            host=settings.mqtt_broker_host,
            port=settings.mqtt_broker_port,
            session_factory=async_session_factory,
            event_bus=event_bus,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            usage_meter=usage_meter,
            use_tls=settings.mqtt_use_tls,
            tls_ca_path=settings.mqtt_tls_ca_path or None,
        )
        try:
            await subscriber.run()
        except asyncio.CancelledError:
            logger.info("MQTT subscriber stopped")
            raise
        except Exception:
            logger.exception("MQTT subscriber crashed; restarting in %.1fs", backoff_s)
        else:
            logger.warning(
                "MQTT subscriber returned without error; restarting in %.1fs",
                backoff_s,
            )
        try:
            await asyncio.sleep(backoff_s)
        except asyncio.CancelledError:
            logger.info("MQTT subscriber stopped during backoff")
            raise
        backoff_s = min(backoff_s * 2, backoff_max_s)


app = FastAPI(
    title="TagPulse",
    description="IoT platform for RFID tag readers and sensor data",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    # allow_origin_regex covers Azure Static Web App preview slot URLs that
    # are allocated per-PR and cannot be pre-enumerated. Empty string in
    # Settings becomes None here so Starlette's default "no regex" applies.
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=[m.strip() for m in settings.cors_allow_methods.split(",") if m.strip()],
    allow_headers=[h.strip() for h in settings.cors_allow_headers.split(",") if h.strip()],
    expose_headers=["X-Request-ID"],
    # Sprint 25 A2: cache OPTIONS preflight responses on the browser to cut
    # cold-tab first-paint-to-login latency. See Settings.cors_preflight_max_age_seconds.
    max_age=settings.cors_preflight_max_age_seconds,
)

# Sprint 22 A4: global per-(tenant, route_class) rate limiter. Bypass list
# in tagpulse.core.rate_limit covers /health, /metrics, /auth/login, docs.
app.middleware("http")(rate_limit_middleware)


@app.middleware("http")
async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach a unique request ID to every request for log correlation."""
    request_id = request.headers.get("X-Request-ID", str(uuid_mod.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Sprint 16 §4 — explicit ingest payload size limit. Per
# docs/design/edge-device-contract.md §3.4 a single MQTT/HTTP message must be
# ≤256 KB after JSON encoding. Reject early via Content-Length so giant payloads
# never hit Pydantic.
_INGEST_PATH_PREFIXES = ("/tag-reads", "/telemetry", "/device-registry")


@app.middleware("http")
async def ingest_payload_size_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.method == "POST" and any(
        request.url.path.startswith(p) for p in _INGEST_PATH_PREFIXES
    ):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > settings.max_ingest_payload_bytes:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Payload exceeds {settings.max_ingest_payload_bytes} "
                            "bytes (edge contract §3.4)"
                        ),
                    },
                )
    return await call_next(request)


@app.middleware("http")
async def usage_metering_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record API usage per tenant for metering and billing."""
    response = await call_next(request)
    tenant_id_header = request.headers.get("X-Tenant-ID")
    if tenant_id_header and hasattr(request.app.state, "usage_meter"):
        try:
            tenant_id = uuid_mod.UUID(tenant_id_header)
        except ValueError:
            return response
        meter: UsageMeter = request.app.state.usage_meter
        path = request.url.path
        if request.method == "POST" and "tag-reads" in path:
            meter.record(tenant_id, "ingestion", "events")
        elif request.method == "POST" and "telemetry" in path:
            meter.record(tenant_id, "telemetry_ingestion", "readings")
        elif request.method in {"GET", "HEAD"}:
            meter.record(tenant_id, "api_read", "requests")
        elif request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            meter.record(tenant_id, "api_write", "requests")
    return response


app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(security_router)
app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(devices_router)
app.include_router(antennas_router)
app.include_router(query_router)
app.include_router(rules_router)
app.include_router(analytics_router)
app.include_router(integrations_router)
app.include_router(sse_router)
app.include_router(telemetry_router)
app.include_router(telemetry_models_router)
app.include_router(admin_router)
app.include_router(admin_ops_router)
app.include_router(users_router)
app.include_router(provisioning_router)
app.include_router(sites_zones_router)
app.include_router(assets_router)
app.include_router(inventory_router)
app.include_router(inventory_imports_router)
app.include_router(tenant_config_router)
app.include_router(tenant_branding_router)
app.include_router(ui_config_router)
app.include_router(categories_router)
app.include_router(labels_router)
app.include_router(tags_router)
app.include_router(bulk_operations_router)
app.include_router(dashboard_router)
