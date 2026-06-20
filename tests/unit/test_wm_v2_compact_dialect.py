"""Sprint 67 — WM compact dialect (``v:2``) conformance + parser unit tests.

Covers ``docs/design/edge-wire-format-v2.md`` §12: the opt-in positional
dialect selected by the reserved envelope field ``v == 2``. The parser
tests exercise :func:`tagpulse.ingestion.wm_wire_format.parse_wm_v2`
directly; the subscriber tests drive the real :class:`MqttSubscriber`
dispatch (``_handle_tag_read`` → ``_handle_wm_v2_compact_message``) and
the real presence reconciler with a scripted fake session, mirroring
:mod:`tests.unit.test_wm_v2_conformance`.

Key invariants asserted here:

- One uniform 5-tuple ``[epc, rssi, cnt, tmp, hum]`` for snap/add/delete.
- ``t=2`` delete ignores the (null/0) reading slots, writes no ``tag_reads``.
- Float ``rssi`` is preserved on the ``tag_reads`` row (``signal_strength``)
  while the ``tag_presence`` lowering rounds it to the SmallInteger column.
- Envelope ``ant`` becomes ``reader_antenna``; ``fw`` rides on ``tag_data._fw``.
- ``v != 2`` and malformed fields route to the DLQ with the spec §6 reason.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.ingestion.mqtt_subscriber import MqttSubscriber
from tagpulse.ingestion.wm_wire_format import WmV2ParseError, parse_wm_v2

EPC_A = "3034257BF461A84000030D40"
EPC_B = "3034257BF461A84000030D41"
SN = "889bd6fc-2bd3-4936-b0e2-fddfbd9fe5dc"
TS_ISO = "2026-06-19T20:24:16Z"


# ---------------------------------------------------------------------------
# Fakes (mirror tests.unit.test_wm_v2_conformance)
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
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
        self.executed: list[Any] = []
        self._selects = list(select_results or [])

    async def execute(self, stmt: Any, params: Any | None = None) -> _ScalarResult:
        self.executed.append(stmt)
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


def _build_subscriber(session: _FakeSession, bus: _FakeBus) -> tuple[MqttSubscriber, AsyncMock]:
    factory = MagicMock(return_value=_SessionCtx(session))
    sub = MqttSubscriber(
        host="broker.example",
        port=1883,
        session_factory=factory,
        event_bus=bus,
        usage_meter=None,
    )
    ingest = AsyncMock()
    sub._build_ingestion_service = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(ingest=ingest)
    )
    sub._persist_mqtt_drop = AsyncMock()  # type: ignore[method-assign]
    return sub, ingest


def _mqtt(topic: str, body: Any) -> Any:
    return SimpleNamespace(topic=topic, payload=json.dumps(body).encode())


def _events(bus: _FakeBus, topic: Topic) -> list[str]:
    return [ev.payload["epc"] for t, ev in bus.published if t == topic]


@pytest.fixture
def tenant_id() -> UUID:
    return uuid4()


@pytest.fixture
def device_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Parser unit tests (parse_wm_v2)
# ---------------------------------------------------------------------------


class TestParser:
    def test_snap_tuple_decoded(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "fw": 1.10,
            "ant": 3,
            "epcs": [[EPC_A, -61.6, 3, -4.0, 57.4], [EPC_B, -59.9, 1, -3.7, 52.9]],
        }
        msg = parse_wm_v2(raw)
        assert msg.t == 0
        assert msg.sn == SN
        assert msg.ant == 3
        assert msg.fw == pytest.approx(1.10)
        assert msg.ts.tzinfo is not None
        assert msg.ts.year == 2026 and msg.ts.hour == 20
        assert [e.epc for e in msg.entries] == [EPC_A, EPC_B]
        assert msg.entries[0].rssi == pytest.approx(-61.6)
        assert msg.entries[0].cnt == 3
        assert msg.entries[0].tmp == pytest.approx(-4.0)
        assert msg.entries[0].hum == pytest.approx(57.4)

    def test_delete_ignores_reading_slots_null(self) -> None:
        raw = {
            "v": 2,
            "t": 2,
            "sn": SN,
            "ts": TS_ISO,
            "epcs": [[EPC_A, None, None, None, None]],
        }
        msg = parse_wm_v2(raw)
        assert msg.t == 2
        e = msg.entries[0]
        assert e.epc == EPC_A
        assert (e.rssi, e.cnt, e.tmp, e.hum) == (None, None, None, None)

    def test_delete_ignores_reading_slots_zero(self) -> None:
        # The [CONFIRM WM] case: zero placeholders are accepted, same as null.
        raw = {"v": 2, "t": 2, "sn": SN, "ts": TS_ISO, "epcs": [[EPC_A, 0, 0, 0, 0]]}
        msg = parse_wm_v2(raw)
        e = msg.entries[0]
        assert e.epc == EPC_A
        assert (e.rssi, e.cnt, e.tmp, e.hum) == (None, None, None, None)

    def test_epc_normalized_uppercase(self) -> None:
        raw = {"v": 2, "t": 2, "sn": SN, "ts": TS_ISO, "epcs": [[EPC_A.lower(), 0, 0, 0, 0]]}
        assert parse_wm_v2(raw).entries[0].epc == EPC_A

    def test_unknown_version_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 3, "t": 0, "sn": SN, "ts": TS_ISO, "epcs": []})
        assert exc.value.reason == "unknown_wire_version"

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "t": 9, "sn": SN, "ts": TS_ISO, "epcs": []})
        assert exc.value.reason == "unknown_type"

    def test_missing_type_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "sn": SN, "ts": TS_ISO, "epcs": []})
        assert exc.value.reason == "missing_type"

    def test_bad_timestamp_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "t": 0, "sn": SN, "ts": "not-a-date", "epcs": []})
        assert exc.value.reason == "invalid_timestamp"

    def test_missing_sn_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "t": 0, "sn": "", "ts": TS_ISO, "epcs": []})
        assert exc.value.reason == "missing_required_field"

    def test_bad_tuple_length_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "t": 0, "sn": SN, "ts": TS_ISO, "epcs": [[EPC_A, -50, 1]]})
        assert exc.value.reason == "invalid_snap_entry"

    def test_invalid_epc_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2({"v": 2, "t": 0, "sn": SN, "ts": TS_ISO, "epcs": [["XYZ", -50, 1, 0, 0]]})
        assert exc.value.reason == "invalid_epc"

    def test_antenna_out_of_range_rejected(self) -> None:
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(
                {
                    "v": 2,
                    "t": 0,
                    "sn": SN,
                    "ts": TS_ISO,
                    "ant": 999,
                    "epcs": [[EPC_A, -50, 1, 0, 0]],
                }
            )
        assert exc.value.reason == "invalid_snap_entry"


# ---------------------------------------------------------------------------
# Forward-compat hardening (fw opaque, append-tolerant tuple)
# ---------------------------------------------------------------------------


class TestForwardCompat:
    def test_fw_string_accepted(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "fw": "1.10.2",
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        assert parse_wm_v2(raw).fw == "1.10.2"

    def test_fw_number_still_accepted(self) -> None:
        raw = {"v": 2, "t": 0, "sn": SN, "ts": TS_ISO, "fw": 1.10, "epcs": [[EPC_A, -50, 1, 0, 0]]}
        assert parse_wm_v2(raw).fw == pytest.approx(1.10)

    def test_fw_structured_rejected(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "fw": {"x": 1},
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "invalid_snap_entry"

    def test_tuple_with_trailing_extra_accepted(self) -> None:
        # 6-element tuple — a future trailing slot (e.g. rpk) is ignored, first 5 used.
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "ant": 3,
            "epcs": [[EPC_A, -61.6, 3, -4.0, 57.4, -55.0]],
        }
        e = parse_wm_v2(raw).entries[0]
        assert e.epc == EPC_A
        assert e.rssi == pytest.approx(-61.6)
        assert e.cnt == 3
        assert e.tmp == pytest.approx(-4.0)
        assert e.hum == pytest.approx(57.4)

    def test_tuple_too_short_rejected(self) -> None:
        raw = {"v": 2, "t": 0, "sn": SN, "ts": TS_ISO, "epcs": [[EPC_A, -50, 1, 0]]}
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "invalid_snap_entry"


# ---------------------------------------------------------------------------
# sn (string or number) + lat/lon range checks
# ---------------------------------------------------------------------------


class TestSnAndCoords:
    def test_sn_string_accepted(self) -> None:
        raw = {"v": 2, "t": 2, "sn": SN, "ts": TS_ISO, "epcs": [[EPC_A, 0, 0, 0, 0]]}
        assert parse_wm_v2(raw).sn == SN

    def test_sn_integer_coerced_to_string(self) -> None:
        raw = {"v": 2, "t": 2, "sn": 4242, "ts": TS_ISO, "epcs": [[EPC_A, 0, 0, 0, 0]]}
        assert parse_wm_v2(raw).sn == "4242"

    def test_sn_float_rejected(self) -> None:
        raw = {"v": 2, "t": 2, "sn": 42.5, "ts": TS_ISO, "epcs": [[EPC_A, 0, 0, 0, 0]]}
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "missing_required_field"

    def test_sn_empty_rejected(self) -> None:
        raw = {"v": 2, "t": 2, "sn": "", "ts": TS_ISO, "epcs": [[EPC_A, 0, 0, 0, 0]]}
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "missing_required_field"

    def test_valid_latlon_accepted(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        msg = parse_wm_v2(raw)
        assert msg.lat == pytest.approx(50.1)
        assert msg.lon == pytest.approx(30.3)

    def test_null_latlon_passes_through(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": None,
            "lon": None,
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        msg = parse_wm_v2(raw)
        assert msg.lat is None and msg.lon is None

    def test_lat_out_of_range_rejected(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 91.0,
            "lon": 30.3,
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "invalid_location"

    def test_lon_out_of_range_rejected(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": -181.0,
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "invalid_location"

    def test_non_number_lat_rejected(self) -> None:
        raw = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": "50.1",
            "lon": 30.3,
            "epcs": [[EPC_A, -50, 1, 0, 0]],
        }
        with pytest.raises(WmV2ParseError) as exc:
            parse_wm_v2(raw)
        assert exc.value.reason == "invalid_location"


# ---------------------------------------------------------------------------
# Subscriber dispatch — snap (t=0)
# ---------------------------------------------------------------------------


class TestSnap:
    @pytest.mark.asyncio
    async def test_snap_reconciles_and_ingests_per_entry(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        # reconcile_snap issues one SELECT for currently-present rows → [].
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "fw": 1.10,
            "ant": 3,
            "epcs": [[EPC_A, -61.6, 3, -4.0, 57.4], [EPC_B, -59.9, 1, -3.7, 52.9]],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        # Both EPCs are new → two appeared events; one ingest per entry.
        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == [EPC_A, EPC_B]
        assert ingest.await_count == 2
        sub._persist_mqtt_drop.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_snap_preserves_float_rssi_and_metadata(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "fw": 1.10,
            "ant": 3,
            "epcs": [[EPC_A, -61.6, 3, -4.0, 57.4]],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        read = ingest.await_args_list[0].args[1]
        assert read.signal_strength == pytest.approx(-61.6)  # float preserved
        assert read.reader_antenna == 3  # envelope ant
        assert read.identity is not None and read.identity.epc_hex == EPC_A
        assert read.tag_data == {"_fw": pytest.approx(1.10)}  # fw not mirrored to telemetry
        assert read.sensor_data == {
            "read_count": 3,
            "temperature_c": pytest.approx(-4.0),
            "humidity_pct": pytest.approx(57.4),
        }
        assert read.location is not None
        assert read.location.latitude == pytest.approx(50.1)
        assert read.location.source == "reader_gnss"

    @pytest.mark.asyncio
    async def test_snap_string_fw_rides_on_tag_data(self, tenant_id: UUID, device_id: UUID) -> None:
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "v": 2,
            "t": 0,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "fw": "1.10.2",  # semver string — stored verbatim, not mirrored to telemetry
            "ant": 3,
            "epcs": [[EPC_A, -61.6, 3, -4.0, 57.4]],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        read = ingest.await_args_list[0].args[1]
        assert read.tag_data == {"_fw": "1.10.2"}


# ---------------------------------------------------------------------------
# Subscriber dispatch — add (t=1) and delete (t=2), batched lists
# ---------------------------------------------------------------------------


class TestAddDelete:
    @pytest.mark.asyncio
    async def test_add_batched_list_appears_per_entry(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        # apply_appeared SELECTs prior status per entry → [] (new) twice.
        session = _FakeSession(select_results=[[], []])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "v": 2,
            "t": 1,
            "sn": SN,
            "ts": TS_ISO,
            "lat": 50.1,
            "lon": 30.3,
            "fw": 1.10,
            "ant": 3,
            "epcs": [[EPC_A, -58.8, 1, -4.2, 55.7], [EPC_B, -60.0, 1, -3.9, 53.3]],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == [EPC_A, EPC_B]
        assert ingest.await_count == 2

    @pytest.mark.asyncio
    async def test_delete_batched_list_no_reads(self, tenant_id: UUID, device_id: UUID) -> None:
        # apply_disappeared SELECTs prior status per entry → present twice.
        session = _FakeSession(select_results=[["present"], ["present"]])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "v": 2,
            "t": 2,
            "sn": SN,
            "ts": TS_ISO,
            "epcs": [[EPC_A, None, None, None, None], [EPC_B, 0, 0, 0, 0]],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == [EPC_A, EPC_B]
        assert ingest.await_count == 0  # delete writes no tag_reads (§4.3)


# ---------------------------------------------------------------------------
# Rejections route to the DLQ with the spec §6 reason
# ---------------------------------------------------------------------------


class TestRejections:
    @pytest.mark.asyncio
    async def test_unknown_version_dropped(self, tenant_id: UUID, device_id: UUID) -> None:
        session = _FakeSession()
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {"v": 3, "t": 0, "sn": SN, "ts": TS_ISO, "epcs": []}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert ingest.await_count == 0
        assert bus.published == []
        drop = sub._persist_mqtt_drop  # type: ignore[attr-defined]
        drop.assert_awaited_once()
        assert drop.await_args.args[4] == "unknown_wire_version"

    @pytest.mark.asyncio
    async def test_bad_tuple_dropped(self, tenant_id: UUID, device_id: UUID) -> None:
        session = _FakeSession()
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {"v": 2, "t": 0, "sn": SN, "ts": TS_ISO, "ant": 3, "epcs": [[EPC_A, -50, 1]]}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert ingest.await_count == 0
        drop = sub._persist_mqtt_drop  # type: ignore[attr-defined]
        drop.assert_awaited_once()
        assert drop.await_args.args[4] == "invalid_snap_entry"


# ---------------------------------------------------------------------------
# Coexistence — a no-`v` message still takes the v2.0 keyed path
# ---------------------------------------------------------------------------


class TestCoexistence:
    @pytest.mark.asyncio
    async def test_v20_keyed_snap_unaffected(self, tenant_id: UUID, device_id: UUID) -> None:
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        # v2.0 keyed snap (no `v`, integer ts, keyed entry with `an`).
        body = {
            "t": 0,
            "sn": 123,
            "ts": 1_716_489_732_001,
            "lat": 41.40338,
            "lon": 2.17403,
            "epcs": [{"an": 1, "epc": EPC_A, "rssi": -48, "cnt": 2}],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == [EPC_A]
        assert ingest.await_count == 1
