"""Sprint 28 D4 + D5 — health detail MQTT freshness + OTel tenant_id stamping."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tagpulse.core import otel_metrics
from tagpulse.core.user_auth import AuthenticatedUser, _annotate_span_with_user


# --- D4 ----------------------------------------------------------------


def test_mqtt_message_age_seconds_returns_none_when_unset() -> None:
    otel_metrics._MQTT_LAST_MESSAGE_TS["value"] = 0.0
    assert otel_metrics.mqtt_message_age_seconds() is None


def test_mqtt_message_age_seconds_returns_age_after_mark() -> None:
    otel_metrics.mark_mqtt_message_processed()
    age = otel_metrics.mqtt_message_age_seconds()
    assert age is not None
    assert 0.0 <= age < 5.0


# --- D5 ----------------------------------------------------------------


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Bind a fresh in-memory exporter to the global tracer provider so
    we can assert on attributes without a real OTLP collector."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch.object(trace, "_TRACER_PROVIDER", provider):
        yield exporter


def test_annotate_span_with_user_jwt(span_exporter: InMemorySpanExporter) -> None:
    tracer = trace.get_tracer("test")
    tid = uuid4()
    uid = uuid4()
    user = AuthenticatedUser(
        user_id=uid,
        tenant_id=tid,
        tenant_name="Acme",
        tenant_slug="acme",
        role="admin",
        email="a@b.com",
    )
    with tracer.start_as_current_span("POST /tag-reads"):
        _annotate_span_with_user(user)

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes or {}
    assert attrs["tenant_id"] == str(tid)
    assert attrs["tenant_slug"] == "acme"
    assert attrs["user_role"] == "admin"
    assert attrs["user_id"] == str(uid)


def test_annotate_span_with_user_xtenant_no_user_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    tracer = trace.get_tracer("test")
    tid = uuid4()
    user = AuthenticatedUser(
        user_id=None,
        tenant_id=tid,
        tenant_name="Acme",
        tenant_slug="acme",
        role="viewer",
    )
    with tracer.start_as_current_span("GET /devices"):
        _annotate_span_with_user(user)

    spans = span_exporter.get_finished_spans()
    attrs = spans[0].attributes or {}
    assert attrs["tenant_id"] == str(tid)
    assert attrs["user_role"] == "viewer"
    assert "user_id" not in attrs


def test_annotate_span_noop_when_no_active_span() -> None:
    """Outside a span context the helper must not raise."""
    user = AuthenticatedUser(
        user_id=None,
        tenant_id=uuid4(),
        tenant_name="x",
        tenant_slug="x",
        role="viewer",
    )
    _annotate_span_with_user(user)  # NonRecordingSpan — silently skipped
