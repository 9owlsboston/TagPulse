"""Sprint 28 C1 — MQTT subscriber operational metrics.

Validates the three OTel objects added in Sprint 28 C1:

* ``mqtt_reconnect_attempts_total`` — bumped on every connect attempt
  with a 'reason' label.
* ``mqtt_messages_rejected_total`` — bumped at every drop point in the
  message handlers, labelled ``topic_kind`` + ``reason``.
* ``mqtt_subscriber_last_message_age_seconds`` — observable gauge that
  reports the wall-clock age of the last processed message.

Uses an in-memory metric reader so the tests don't depend on any
exporter wiring.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry import metrics as otel_metrics_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from tagpulse.core import otel_metrics
from tagpulse.ingestion import mqtt_subscriber as sub_mod
from tagpulse.ingestion.mqtt_subscriber import (
    MqttSubscriber,
    _classify_connect_error,
    _record_rejection,
)

# ---------- shared meter wiring -----------------------------------------------


@pytest.fixture(scope="module")
def reader() -> InMemoryMetricReader:
    """Re-bind the global OTel meter so the module-level counters write
    into an in-memory reader the test can assert on. Done once per module
    because OTel installs a default NoOp provider on first use, which the
    counters and gauge close over."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics_api.set_meter_provider(provider)

    # Re-create the counters/gauges against the fresh provider.
    new_meter = otel_metrics_api.get_meter("tagpulse")
    otel_metrics.meter = new_meter
    otel_metrics.mqtt_reconnect_attempts_counter = new_meter.create_counter(
        "tagpulse_mqtt_reconnect_attempts_total"
    )
    otel_metrics.mqtt_messages_rejected_counter = new_meter.create_counter(
        "tagpulse_mqtt_messages_rejected_total"
    )
    otel_metrics.mqtt_subscriber_last_message_age_seconds = new_meter.create_observable_gauge(
        name="tagpulse_mqtt_subscriber_last_message_age_seconds",
        callbacks=[otel_metrics._observe_mqtt_age],  # noqa: SLF001
    )
    # The subscriber module captured the ORIGINAL counter at import time;
    # rebind it so _record_rejection uses the test's reader.
    sub_mod.mqtt_messages_rejected_counter = otel_metrics.mqtt_messages_rejected_counter
    sub_mod.mqtt_reconnect_attempts_counter = otel_metrics.mqtt_reconnect_attempts_counter
    return reader


def _collect(reader: InMemoryMetricReader) -> dict[str, list[tuple[dict[str, str], int | float]]]:
    """Drain the reader and return ``{metric_name: [(labels, value), ...]}``."""
    data = reader.get_metrics_data()
    out: dict[str, list[tuple[dict[str, str], int | float]]] = {}
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                points = getattr(m.data, "data_points", [])
                for p in points:
                    # Counter/Sum/Gauge points have .value;
                    # Histogram points have .sum (skip — none here).
                    val = getattr(p, "value", None)
                    if val is None:
                        continue
                    out.setdefault(m.name, []).append((dict(p.attributes or {}), val))
    return out


# ---------- _classify_connect_error -------------------------------------------


class _NotAuthorizedError(Exception):
    """Stand-in for aiomqtt's NotAuthorizedError-style exceptions."""


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("nope"), "timeout"),
        (ConnectionRefusedError("refused"), "connection_refused"),
        (ConnectionError("broken"), "connection_refused"),
        (_NotAuthorizedError("bad creds"), "auth_failed"),
        (RuntimeError("???"), "other"),
    ],
)
def test_classify_connect_error(exc: BaseException, expected: str) -> None:
    assert _classify_connect_error(exc) == expected


# ---------- _record_rejection -------------------------------------------------


def test_record_rejection_labels(reader: InMemoryMetricReader) -> None:
    _record_rejection("tag_read", "invalid_json")
    _record_rejection("tag_read", "invalid_json")
    _record_rejection("subject_telemetry", "invalid_schema")

    snapshot = _collect(reader)
    rejected = snapshot.get("tagpulse_mqtt_messages_rejected_total", [])
    by_label = {(p[0].get("topic_kind"), p[0].get("reason")): p[1] for p in rejected}
    assert by_label.get(("tag_read", "invalid_json")) == 2
    assert by_label.get(("subject_telemetry", "invalid_schema")) == 1


# ---------- mark_mqtt_message_processed --------------------------------------


def test_mark_mqtt_message_processed_updates_gauge(
    reader: InMemoryMetricReader,
) -> None:
    otel_metrics._MQTT_LAST_MESSAGE_TS["value"] = 0.0  # noqa: SLF001
    snapshot = _collect(reader)
    # Before any mark, the gauge emits no observations.
    age_points = snapshot.get("tagpulse_mqtt_subscriber_last_message_age_seconds", [])
    assert age_points == []

    otel_metrics.mark_mqtt_message_processed()
    time.sleep(0.05)
    snapshot2 = _collect(reader)
    age_points2 = snapshot2.get("tagpulse_mqtt_subscriber_last_message_age_seconds", [])
    assert len(age_points2) >= 1
    age_value = age_points2[0][1]
    assert 0.04 <= age_value < 5.0  # generous upper bound for slow CI


# ---------- MqttSubscriber.run reconnect path --------------------------------


class _RaisingClient:
    """aiomqtt.Client stand-in that raises on enter to simulate broker down."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _RaisingClient:
        raise ConnectionRefusedError("broker down")

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_run_increments_reconnect_counter_on_failure(
    monkeypatch: pytest.MonkeyPatch, reader: InMemoryMetricReader
) -> None:
    """One startup + one classified retry should be recorded before we cancel."""

    monkeypatch.setattr(sub_mod.aiomqtt, "Client", _RaisingClient)
    # Skip the backoff sleep so we get a 2nd attempt quickly. Capture the
    # real sleep first, otherwise the patched asyncio.sleep recurses.
    real_sleep = sub_mod.asyncio.sleep
    sleeps: list[float] = []

    async def _fast_sleep(d: float) -> None:
        sleeps.append(d)
        await real_sleep(0)

    monkeypatch.setattr(sub_mod.asyncio, "sleep", _fast_sleep)

    subscriber = MqttSubscriber(
        host="broker",
        port=1883,
        session_factory=None,  # type: ignore[arg-type]  # never used on connect path
        event_bus=None,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(subscriber.run())
    # Allow several connect attempts and poll the reader directly so the
    # test isn't tied to sleep-call counts.
    by_reason: dict[str, int | float] = {}
    for _ in range(200):
        await asyncio.sleep(0)
        snapshot = _collect(reader)
        attempts = snapshot.get("tagpulse_mqtt_reconnect_attempts_total", [])
        by_reason = {}
        for labels, value in attempts:
            by_reason[labels.get("reason", "?")] = value
        if by_reason.get("startup", 0) >= 1 and by_reason.get("connection_refused", 0) >= 1:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert by_reason.get("startup", 0) >= 1
    assert by_reason.get("connection_refused", 0) >= 1


# ---------- _handle_message rejection paths ----------------------------------


class _FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


@pytest.mark.asyncio
async def test_handle_message_records_rejections(
    reader: InMemoryMetricReader,
) -> None:
    sub = MqttSubscriber(
        host="x",
        port=1883,
        session_factory=None,  # type: ignore[arg-type]
        event_bus=None,  # type: ignore[arg-type]
    )

    # Unparseable topic
    await sub._handle_message(_FakeMessage("garbage/topic", b"{}"))  # noqa: SLF001
    # Unknown suffix on a valid 5-segment topic
    bad_suffix = f"tenants/{uuid4()}/devices/{uuid4()}/wat"
    await sub._handle_message(_FakeMessage(bad_suffix, b"{}"))  # noqa: SLF001
    # tag-read with invalid JSON
    tr_topic = f"tenants/{uuid4()}/devices/{uuid4()}/tag-reads"
    await sub._handle_message(_FakeMessage(tr_topic, b"not-json"))  # noqa: SLF001

    snapshot = _collect(reader)
    rejected = snapshot.get("tagpulse_mqtt_messages_rejected_total", [])
    by_label = {(p[0].get("topic_kind"), p[0].get("reason")): p[1] for p in rejected}
    assert by_label.get(("unparseable", "invalid_topic"), 0) >= 1
    assert by_label.get(("unknown_suffix", "unknown_suffix"), 0) >= 1
    assert by_label.get(("tag_read", "invalid_json"), 0) >= 1


# Suppress unused-import warnings in the linter for the AsyncIterator alias.
_ = AsyncIterator
_ = json
