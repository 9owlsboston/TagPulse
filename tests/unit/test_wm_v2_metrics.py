"""Sprint 46 Phase E — v2 wire-format and presence-reconciler OTel counters.

Validates the seven new metric objects added in Phase E (see
:mod:`tagpulse.core.otel_metrics` and the call sites in
:mod:`tagpulse.ingestion.mqtt_subscriber` and
:mod:`tagpulse.ingestion.presence_reconciler`):

- ``tagpulse_mqtt_wm_rejections_total{reason}``
- ``tagpulse_mqtt_wm_snap_large_total{sn}``
- ``tagpulse_mqtt_wm_sub_no_presence_total``
- ``tagpulse_presence_reconcile_duration_seconds{t}`` (histogram)
- ``tagpulse_presence_entries_total{status}``
- ``tagpulse_signaling_tag_appeared_total{source}``
- ``tagpulse_signaling_tag_disappeared_total{source}``

Each test drives the real call site (subscriber dispatch or reconciler
coroutine) against scripted fakes, then asserts the in-memory metric
reader captured the expected datapoints. Counter-rebinding pattern
mirrors :mod:`tests.unit.test_sprint28_mqtt_metrics`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from tagpulse.core import otel_metrics
from tagpulse.ingestion import mqtt_subscriber as sub_mod
from tagpulse.ingestion import presence_reconciler as reconciler_mod
from tagpulse.ingestion.mqtt_subscriber import MqttSubscriber
from tagpulse.ingestion.wm_wire_format import SNAP_SOFT_CAP_ENTRIES

TS_MS = 1_716_489_732_001


# ---------------------------------------------------------------------------
# Meter rebind so module-level counters write into our reader
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # NOTE: do NOT call otel_metrics_api.set_meter_provider(...) — it is
    # one-shot per process, and test_sprint28_mqtt_metrics installs the global
    # provider first. Pull the meter directly off our provider so our counters
    # bind into our reader regardless of who won the global race.
    new_meter = provider.get_meter("tagpulse")
    otel_metrics.meter = new_meter

    otel_metrics.mqtt_wm_rejections_counter = new_meter.create_counter(
        "tagpulse_mqtt_wm_rejections_total"
    )
    otel_metrics.mqtt_wm_snap_large_counter = new_meter.create_counter(
        "tagpulse_mqtt_wm_snap_large_total"
    )
    otel_metrics.mqtt_wm_sub_no_presence_counter = new_meter.create_counter(
        "tagpulse_mqtt_wm_sub_no_presence_total"
    )
    otel_metrics.presence_reconcile_duration_seconds = new_meter.create_histogram(
        "tagpulse_presence_reconcile_duration_seconds"
    )
    otel_metrics.presence_entries_counter = new_meter.create_counter(
        "tagpulse_presence_entries_total"
    )
    otel_metrics.signaling_tag_appeared_counter = new_meter.create_counter(
        "tagpulse_signaling_tag_appeared_total"
    )
    otel_metrics.signaling_tag_disappeared_counter = new_meter.create_counter(
        "tagpulse_signaling_tag_disappeared_total"
    )

    # Rebind the objects the subscriber + reconciler captured at import.
    sub_mod.mqtt_wm_rejections_counter = otel_metrics.mqtt_wm_rejections_counter
    sub_mod.mqtt_wm_snap_large_counter = otel_metrics.mqtt_wm_snap_large_counter
    sub_mod.presence_reconcile_duration_seconds = otel_metrics.presence_reconcile_duration_seconds
    reconciler_mod.mqtt_wm_sub_no_presence_counter = otel_metrics.mqtt_wm_sub_no_presence_counter
    reconciler_mod.presence_entries_counter = otel_metrics.presence_entries_counter
    reconciler_mod.signaling_tag_appeared_counter = otel_metrics.signaling_tag_appeared_counter
    reconciler_mod.signaling_tag_disappeared_counter = (
        otel_metrics.signaling_tag_disappeared_counter
    )
    return reader


def _collect_counters(
    reader: InMemoryMetricReader,
) -> dict[str, list[tuple[dict[str, str], int | float]]]:
    """Drain counter/gauge points."""
    out: dict[str, list[tuple[dict[str, str], int | float]]] = {}
    data = reader.get_metrics_data()
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for p in getattr(m.data, "data_points", []):
                    val = getattr(p, "value", None)
                    if val is None:
                        continue
                    out.setdefault(m.name, []).append((dict(p.attributes or {}), val))
    return out


def _collect_histogram(
    reader: InMemoryMetricReader, name: str
) -> list[tuple[dict[str, str], int, float]]:
    """Drain histogram points → ``[(labels, count, sum), ...]``."""
    out: list[tuple[dict[str, str], int, float]] = []
    data = reader.get_metrics_data()
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for p in getattr(m.data, "data_points", []):
                    out.append((dict(p.attributes or {}), p.count, p.sum))
    return out


# ---------------------------------------------------------------------------
# Fakes (slim copies of tests/unit/test_wm_v2_conformance.py helpers)
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, topic: Any, event: Any) -> None:
        self.published.append((topic, event))


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any]]:
        return [(r,) for r in self._rows]

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, select_results: list[list[Any]] | None = None) -> None:
        self._selects = list(select_results or [])

    async def execute(self, stmt: Any, params: Any | None = None) -> _ScalarResult:
        if type(stmt).__name__ == "Select":
            return _ScalarResult(self._selects.pop(0) if self._selects else [])
        return _ScalarResult([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _SessionCtx:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        return None


def _build_subscriber(session: _FakeSession, bus: _FakeBus) -> MqttSubscriber:
    factory = MagicMock(return_value=_SessionCtx(session))
    sub = MqttSubscriber(
        host="b", port=1883, session_factory=factory, event_bus=bus, usage_meter=None
    )
    sub._build_ingestion_service = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(ingest=AsyncMock())
    )
    sub._persist_mqtt_drop = AsyncMock()  # type: ignore[method-assign]
    return sub


def _mqtt(topic: str, body: Any) -> Any:
    return SimpleNamespace(topic=topic, payload=json.dumps(body).encode())


@pytest.fixture
def tenant_id() -> UUID:
    return uuid4()


@pytest.fixture
def device_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Rejection counter — spec §6
# ---------------------------------------------------------------------------


class TestRejectionsCounter:
    @pytest.mark.asyncio
    async def test_invalid_epc_bumps_with_reason_label(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        sub = _build_subscriber(_FakeSession(), _FakeBus())
        body = {
            "t": 1,
            "sn": 1,
            "ts": TS_MS,
            "lat": 1.0,
            "lon": 2.0,
            "an": 1,
            "epc": "ZZZ",  # non-hex → invalid_epc
            "rssi": -50,
            "cnt": 1,
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        points = _collect_counters(reader).get("tagpulse_mqtt_wm_rejections_total", [])
        bumped = [v for (labels, v) in points if labels.get("reason") == "invalid_epc"]
        assert sum(bumped) >= 1


# ---------------------------------------------------------------------------
# Soft-cap counter — spec §6
# ---------------------------------------------------------------------------


class TestSnapLargeCounter:
    @pytest.mark.asyncio
    async def test_above_soft_cap_bumps_with_sn_label(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        sub = _build_subscriber(session, bus)
        entries = [
            {"an": 1, "epc": f"E280116{i:09X}", "rssi": -50, "cnt": 1}
            for i in range(SNAP_SOFT_CAP_ENTRIES + 1)
        ]
        body = {
            "t": 0,
            "sn": 7777,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": entries,
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        points = _collect_counters(reader).get("tagpulse_mqtt_wm_snap_large_total", [])
        bumped = [v for (labels, v) in points if labels.get("sn") == "7777"]
        assert sum(bumped) >= 1

    @pytest.mark.asyncio
    async def test_below_soft_cap_does_not_bump(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        baseline = sum(
            v for (_, v) in _collect_counters(reader).get("tagpulse_mqtt_wm_snap_large_total", [])
        )
        session = _FakeSession(select_results=[[]])
        sub = _build_subscriber(session, _FakeBus())
        body = {
            "t": 0,
            "sn": 8888,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [{"an": 1, "epc": "E2801160AAAA", "rssi": -50, "cnt": 1}],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))
        after = sum(
            v for (_, v) in _collect_counters(reader).get("tagpulse_mqtt_wm_snap_large_total", [])
        )
        assert after == baseline


# ---------------------------------------------------------------------------
# Sub-no-presence counter — spec §6
# ---------------------------------------------------------------------------


class TestSubNoPresenceCounter:
    @pytest.mark.asyncio
    async def test_t2_for_unknown_epc_bumps_counter(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        baseline = sum(
            v
            for (_, v) in _collect_counters(reader).get(
                "tagpulse_mqtt_wm_sub_no_presence_total", []
            )
        )
        session = _FakeSession(select_results=[[]])  # never-seen
        sub = _build_subscriber(session, _FakeBus())
        body = {"t": 2, "sn": 1, "ts": TS_MS, "epc": "E2801160DEAD"}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))
        after = sum(
            v
            for (_, v) in _collect_counters(reader).get(
                "tagpulse_mqtt_wm_sub_no_presence_total", []
            )
        )
        assert after - baseline == 1


# ---------------------------------------------------------------------------
# Presence-entries counter (status=present|gone)
# ---------------------------------------------------------------------------


class TestPresenceEntriesCounter:
    @pytest.mark.asyncio
    async def test_snap_bumps_present_and_gone(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        # current=[BBBB, CCCC] vs snap=[AAAA, BBBB] → present+=2, gone+=1.
        session = _FakeSession(select_results=[["E2801160BBBB", "E2801160CCCC"]])
        sub = _build_subscriber(session, _FakeBus())
        body = {
            "t": 0,
            "sn": 1,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [
                {"an": 1, "epc": "E2801160AAAA", "rssi": -50, "cnt": 1},
                {"an": 1, "epc": "E2801160BBBB", "rssi": -50, "cnt": 1},
            ],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        points = _collect_counters(reader).get("tagpulse_presence_entries_total", [])
        # The reader accumulates across all module tests; check that the
        # status labels exist and were recorded with the right totals.
        present_total = sum(v for (lbl, v) in points if lbl.get("status") == "present")
        gone_total = sum(v for (lbl, v) in points if lbl.get("status") == "gone")
        # At least the 2 present + 1 gone from this call must show up
        # (earlier tests may have contributed more).
        assert present_total >= 2
        assert gone_total >= 1


# ---------------------------------------------------------------------------
# Signaling counters (per-topic + source label)
# ---------------------------------------------------------------------------


class TestSignalingCounters:
    @pytest.mark.asyncio
    async def test_snap_appeared_and_disappeared_bumps_with_source_snap(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        # current=[XXXX] vs snap=[AAAA] → 1 appeared, 1 disappeared, both source="snap".
        session = _FakeSession(select_results=[["E2801160XXXX"]])
        sub = _build_subscriber(session, _FakeBus())
        body = {
            "t": 0,
            "sn": 1,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [{"an": 1, "epc": "E2801160AAAA", "rssi": -50, "cnt": 1}],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        snap_app = sum(
            v
            for (lbl, v) in _collect_counters(reader).get(
                "tagpulse_signaling_tag_appeared_total", []
            )
            if lbl.get("source") == "snap"
        )
        snap_gone = sum(
            v
            for (lbl, v) in _collect_counters(reader).get(
                "tagpulse_signaling_tag_disappeared_total", []
            )
            if lbl.get("source") == "snap"
        )
        assert snap_app >= 1
        assert snap_gone >= 1

    @pytest.mark.asyncio
    async def test_delta_appeared_bumps_with_source_delta(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        baseline = sum(
            v
            for (lbl, v) in _collect_counters(reader).get(
                "tagpulse_signaling_tag_appeared_total", []
            )
            if lbl.get("source") == "delta"
        )
        session = _FakeSession(select_results=[[]])  # new EPC
        sub = _build_subscriber(session, _FakeBus())
        body = {
            "t": 1,
            "sn": 1,
            "ts": TS_MS,
            "lat": 1.0,
            "lon": 2.0,
            "an": 1,
            "epc": "E2801160AAAA",
            "rssi": -50,
            "cnt": 1,
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))
        after = sum(
            v
            for (lbl, v) in _collect_counters(reader).get(
                "tagpulse_signaling_tag_appeared_total", []
            )
            if lbl.get("source") == "delta"
        )
        assert after - baseline == 1


# ---------------------------------------------------------------------------
# Reconcile-duration histogram (labelled by t)
# ---------------------------------------------------------------------------


class TestReconcileDurationHistogram:
    @pytest.mark.asyncio
    async def test_snap_records_with_t_snap_label(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        session = _FakeSession(select_results=[[]])
        sub = _build_subscriber(session, _FakeBus())
        body = {
            "t": 0,
            "sn": 1,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [{"an": 1, "epc": "E2801160AAAA", "rssi": -50, "cnt": 1}],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        points = _collect_histogram(reader, "tagpulse_presence_reconcile_duration_seconds")
        snap_points = [p for p in points if p[0].get("t") == "snap"]
        assert snap_points, "expected at least one t=snap histogram point"
        # Count and sum are monotonically increasing across the module.
        labels, count, total = snap_points[0]
        assert count >= 1
        assert total >= 0.0

    @pytest.mark.asyncio
    async def test_appeared_and_disappeared_label_distinct_buckets(
        self, reader: InMemoryMetricReader, tenant_id: UUID, device_id: UUID
    ) -> None:
        session = _FakeSession(select_results=[[], ["present"]])
        sub = _build_subscriber(session, _FakeBus())
        appeared_body = {
            "t": 1,
            "sn": 1,
            "ts": TS_MS,
            "lat": 1.0,
            "lon": 2.0,
            "an": 1,
            "epc": "E2801160AAAA",
            "rssi": -50,
            "cnt": 1,
        }
        disappeared_body = {"t": 2, "sn": 1, "ts": TS_MS, "epc": "E2801160AAAA"}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", appeared_body))
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", disappeared_body))

        points = _collect_histogram(reader, "tagpulse_presence_reconcile_duration_seconds")
        seen_labels = {p[0].get("t") for p in points}
        assert "appeared" in seen_labels
        assert "disappeared" in seen_labels
