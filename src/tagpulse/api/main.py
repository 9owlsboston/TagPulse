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
from tagpulse.api.routes.assets import router as assets_router
from tagpulse.api.routes.auth import router as auth_router
from tagpulse.api.routes.devices import router as devices_router
from tagpulse.api.routes.health import router as health_router
from tagpulse.api.routes.ingestion import router as ingestion_router
from tagpulse.api.routes.integrations import router as integrations_router
from tagpulse.api.routes.inventory import router as inventory_router
from tagpulse.api.routes.metrics import router as metrics_router
from tagpulse.api.routes.provisioning import router as provisioning_router
from tagpulse.api.routes.query import router as query_router
from tagpulse.api.routes.rules import router as rules_router
from tagpulse.api.routes.sites_zones import router as sites_zones_router
from tagpulse.api.routes.telemetry import router as telemetry_router
from tagpulse.api.routes.telemetry_models import router as telemetry_models_router
from tagpulse.api.routes.users import router as users_router
from tagpulse.core.config import settings
from tagpulse.core.logging import setup_logging
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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle — start/stop EventBus and MQTT subscriber."""
    setup_logging(settings.log_level)
    setup_telemetry(app)

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

    # Rule evaluator — subscribes to tag read events
    evaluator = RuleEvaluator(
        session_factory=async_session_factory,
        event_bus=event_bus,
        usage_meter=usage_meter,
    )
    await event_bus.subscribe(Topic.TAG_READ_CREATED, evaluator.on_tag_read)

    # Alert delivery — subscribes to alert triggered events
    alert_delivery = AlertDeliveryService()
    await alert_delivery.start()
    await event_bus.subscribe(
        Topic.ALERT_TRIGGERED, alert_delivery.on_alert_triggered
    )
    app.state.alert_delivery = alert_delivery

    # Analytics modules
    analytics_modules: list[AnalyticsModule] = [
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

    # Start EventBus AFTER all subscribers are registered
    await event_bus.start()

    # MQTT subscriber background task
    mqtt_task: asyncio.Task[None] | None = None
    if settings.mqtt_broker_host:
        mqtt_task = asyncio.create_task(
            _run_mqtt_subscriber(event_bus)
        )
        logger.info("MQTT subscriber task started")

    yield

    # Shutdown
    if mqtt_task is not None:
        mqtt_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mqtt_task
    await usage_meter.stop()
    for module in analytics_modules:
        await module.stop()
    await webhook_dispatcher.stop()
    await alert_delivery.stop()
    await event_bus.drain(timeout=10.0)


async def _run_mqtt_subscriber(event_bus: AsyncEventBus) -> None:
    """Run MQTT subscriber with per-message sessions to avoid stale ORM state."""
    subscriber = MqttSubscriber(
        host=settings.mqtt_broker_host,
        port=settings.mqtt_broker_port,
        session_factory=async_session_factory,
        event_bus=event_bus,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
    )
    try:
        await subscriber.run()
    except asyncio.CancelledError:
        logger.info("MQTT subscriber stopped")
    except Exception:
        logger.exception("MQTT subscriber crashed")


app = FastAPI(
    title="TagPulse",
    description="IoT platform for RFID tag readers and sensor data",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


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
app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(devices_router)
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
