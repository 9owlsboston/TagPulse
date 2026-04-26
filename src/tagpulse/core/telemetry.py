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
    if otlp_endpoint:
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
