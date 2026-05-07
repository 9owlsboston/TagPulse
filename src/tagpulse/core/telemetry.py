"""OpenTelemetry setup — metrics + traces + auto-instrumentation."""

import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)


def setup_telemetry(app: FastAPI) -> None:
    """Initialize OpenTelemetry metrics, traces, and auto-instrumentation."""
    service_name = os.environ.get("OTEL_SERVICE_NAME", "tagpulse")
    resource = Resource.create({"service.name": service_name})

    # Metrics — Prometheus format at /metrics
    reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    appinsights_conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if appinsights_conn:
        # Azure Monitor distro auto-configures traces + metrics + logs to App Insights.
        # When set, takes precedence over OTLP — Container Apps deployments use this path
        # (Sprint 22 Phase C C3). Soft-imported so non-Azure deployments don't need the dep.
        try:
            from azure.monitor.opentelemetry import (  # type: ignore[import-not-found,unused-ignore]
                configure_azure_monitor,
            )

            configure_azure_monitor(
                connection_string=appinsights_conn,
                resource=resource,
            )
            logger.info("Azure Monitor OpenTelemetry distro configured")
        except ImportError:
            logger.warning(
                "APPLICATIONINSIGHTS_CONNECTION_STRING set but "
                "azure-monitor-opentelemetry not installed — install the [azure] extra"
            )
    elif otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )
            logger.info("OTLP trace exporter configured: %s", otlp_endpoint)
        except ImportError:
            logger.warning(
                "OTLP exporter not available — install "
                "opentelemetry-exporter-otlp-proto-grpc for trace export"
            )
    trace.set_tracer_provider(tracer_provider)

    # Auto-instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    # Auto-instrument outbound HTTP (webhook delivery)
    HTTPXClientInstrumentor().instrument()

    # Inject trace IDs into log records
    LoggingInstrumentor().instrument()

    logger.info("OpenTelemetry initialized: service=%s", service_name)
