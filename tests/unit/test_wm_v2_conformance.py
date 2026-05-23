"""Sprint 46 Phase D — wire-format v2 conformance suite.

Drives :class:`MqttSubscriber` end-to-end from MQTT payload bytes through
the v2 dispatch hook (``_handle_tag_read`` → ``_handle_wm_v2_message``),
the presence reconciler, and event emission. Each test maps to one
scenario in ``docs/design/edge-wire-format-v2.md`` §5, plus the
Phase-D additions called out in ``docs/roadmap.md`` (large snap,
v1/v2 coexistence).

Lives in ``tests/unit/`` so ``make check`` runs it (Makefile scope is
``tests/unit``); the conformance label refers to its spec-section
organization rather than to a separate test runner.

Unlike :mod:`tests.unit.test_presence_reconciler` (which exercises the
reconciler in isolation) and :mod:`tests.unit.test_mqtt_subscriber_v2_dispatch`
(which stubs the reconciler), these tests run the *real* reconciler
behind the real subscriber dispatch — only the database session and
ingestion service are faked. SELECT results are scripted so the
reconciler's queries return deterministic ``tag_presence`` snapshots.
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

# ---------------------------------------------------------------------------
# Fakes
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
    """Scripts SELECT results in FIFO order; INSERT/UPDATE no-op."""

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


# ---------------------------------------------------------------------------
# Wiring helpers
# ---------------------------------------------------------------------------


def _build_subscriber(session: _FakeSession, bus: _FakeBus) -> tuple[MqttSubscriber, AsyncMock]:
    """Wire a real :class:`MqttSubscriber` against the fake session + bus.

    Returns ``(subscriber, ingest_mock)`` so the caller can assert on
    ingest call counts and the mapped :class:`TagReadCreate` payloads.
    """
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
    # Make rejection persistence a no-op so unhappy-path tests don't
    # need the audit-log infrastructure.
    sub._persist_mqtt_drop = AsyncMock()  # type: ignore[method-assign]
    return sub, ingest


def _mqtt(topic: str, body: Any) -> Any:
    return SimpleNamespace(topic=topic, payload=json.dumps(body).encode())


def _events(bus: _FakeBus, topic: Topic) -> list[str]:
    return [ev.payload["epc"] for t, ev in bus.published if t == topic]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


TS_MS = 1_716_489_732_001


@pytest.fixture
def tenant_id() -> UUID:
    return uuid4()


@pytest.fixture
def device_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# §5.1 — Steady-state cycle (zero wire messages)
# ---------------------------------------------------------------------------


class TestSpec51SteadyState:
    @pytest.mark.asyncio
    async def test_no_messages_no_writes_no_events(self, tenant_id: UUID, device_id: UUID) -> None:
        """§5.1: a steady-state cycle emits no wire messages.

        Trivially the subscriber receives nothing → no session opens,
        no events emitted. We assert this by constructing a subscriber
        and confirming the bus stays empty.
        """
        session = _FakeSession()
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        # Nothing happens — exercise the absence-of-input case.
        assert bus.published == []
        assert ingest.await_count == 0
        assert session.executed == []
        # The subscriber instance is otherwise inert.
        assert sub is not None


# ---------------------------------------------------------------------------
# §5.2 — 5 new tags + 3 departures via deltas (mixed t=1 / t=2)
# ---------------------------------------------------------------------------


class TestSpec52MixedDeltas:
    @pytest.mark.asyncio
    async def test_five_appeared_three_disappeared(self, tenant_id: UUID, device_id: UUID) -> None:
        # 5 t=1 messages: each SELECT prior status returns [] (new EPC) → emit.
        # 3 t=2 messages: each SELECT prior status returns ["present"] → emit gone.
        select_script: list[list[Any]] = [
            [],  # AAAA t=1 prior
            [],  # BBBB t=1 prior
            [],  # CCCC t=1 prior
            [],  # DDDD t=1 prior
            [],  # EEEE t=1 prior
            ["present"],  # FFFF t=2 prior
            ["present"],  # GGGG t=2 prior
            ["present"],  # HHHH t=2 prior
        ]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        adds = ["E2801160AAAA", "E2801160BBBB", "E2801160CCCC", "E2801160DDDD", "E2801160EEEE"]
        subs = ["E2801160FFFF", "E2801160DEAD", "E2801160BEEF"]

        for i, epc in enumerate(adds):
            body = {
                "t": 1,
                "sn": 123,
                "ts": TS_MS,
                "lat": 41.40338,
                "lon": 2.17403,
                "an": 1 if i % 2 == 0 else 2,
                "epc": epc,
                "rssi": -48,
                "cnt": 2,
            }
            await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        for epc in subs:
            body = {"t": 2, "sn": 123, "ts": TS_MS, "epc": epc}
            await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        # Spec §5.2 outcomes: 5 appeared events, 3 disappeared events.
        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == adds
        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == subs
        # 5 ingest calls for t=1; t=2 writes no tag_reads (§4.3).
        assert ingest.await_count == 5


# ---------------------------------------------------------------------------
# §5.3 — Periodic snapshot reconciles against current present-set
# ---------------------------------------------------------------------------


class TestSpec53PeriodicSnapshot:
    @pytest.mark.asyncio
    async def test_snap_reconciles_against_current_present_set(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """§5.3: snap with 3 entries against current=[B, C, X].

        B, C present in both → no events, just last_seen refresh.
        A in snap but not current → appeared.
        X in current but not snap → gone.
        """
        select_script = [["E2801160B0B0", "E2801160C0C0", "E2801160X0X0"]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": 41.40338,
            "lon": 2.17403,
            "epcs": [
                {"an": 1, "epc": "E2801160A0A0", "rssi": -48, "cnt": 3},
                {"an": 1, "epc": "E2801160B0B0", "rssi": -52, "cnt": 2},
                {"an": 2, "epc": "E2801160C0C0", "rssi": -44, "cnt": 4},
            ],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == ["E2801160A0A0"]
        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["E2801160X0X0"]
        # One tag_reads insert per snap entry (§4.4).
        assert ingest.await_count == 3


# ---------------------------------------------------------------------------
# §5.4 — Empty snapshot marks every present EPC gone
# ---------------------------------------------------------------------------


class TestSpec54EmptySnapshot:
    @pytest.mark.asyncio
    async def test_empty_snap_marks_all_present_gone(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        select_script = [["E2801160AAAA", "E2801160BBBB"]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": 41.40338,
            "lon": 2.17403,
            "epcs": [],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == []
        assert sorted(_events(bus, Topic.SIGNALING_TAG_DISAPPEARED)) == [
            "E2801160AAAA",
            "E2801160BBBB",
        ]
        # No entries → no tag_reads rows.
        assert ingest.await_count == 0


# ---------------------------------------------------------------------------
# §5.5 — Producer reboot: first message after reconnect is a snap
# ---------------------------------------------------------------------------


class TestSpec55ProducerReboot:
    @pytest.mark.asyncio
    async def test_reboot_snap_self_heals_against_empty_present_set(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """§5.5: after reboot the server has no in-memory state.

        Snap reconciles against whatever was in ``tag_presence`` at
        reboot. Here the table happens to be empty (fresh deploy) so
        every snap entry surfaces as appeared.
        """
        select_script = [[]]  # no rows present
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": 41.40338,
            "lon": 2.17403,
            "epcs": [
                {"an": 1, "epc": "E2801160AAAA", "rssi": -48, "cnt": 1},
                {"an": 1, "epc": "E2801160BBBB", "rssi": -50, "cnt": 1},
                {"an": 1, "epc": "E2801160CCCC", "rssi": -52, "cnt": 1},
            ],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert sorted(_events(bus, Topic.SIGNALING_TAG_APPEARED)) == [
            "E2801160AAAA",
            "E2801160BBBB",
            "E2801160CCCC",
        ]
        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == []
        assert ingest.await_count == 3


# ---------------------------------------------------------------------------
# §5.6 — Subscriber outage and recovery: replayed messages are no-ops
# ---------------------------------------------------------------------------


class TestSpec56SubscriberReplay:
    @pytest.mark.asyncio
    async def test_replayed_appeared_for_already_present_is_no_op(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """§5.6: a replayed ``t=1`` for an EPC already in ``present``
        upserts last_seen but does NOT emit a duplicate appeared event.
        """
        select_script = [["present"]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "t": 1,
            "sn": 123,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "an": 1,
            "epc": "E2801160AAAA",
            "rssi": -48,
            "cnt": 1,
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        # No event re-emitted; tag_reads row still flows through (§4.4).
        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == []
        assert ingest.await_count == 1

    @pytest.mark.asyncio
    async def test_replayed_disappeared_for_unknown_epc_is_silent(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """§6: ``t=2`` for never-seen EPC logs + counter only, no event,
        no tag_reads row.
        """
        select_script = [[]]  # no prior row
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {"t": 2, "sn": 123, "ts": TS_MS, "epc": "E2801160DEAD"}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert bus.published == []
        assert ingest.await_count == 0


# ---------------------------------------------------------------------------
# §5.7 — Lost t=2 healed by the next snap
# ---------------------------------------------------------------------------


class TestSpec57LostSubHealedBySnap:
    @pytest.mark.asyncio
    async def test_missed_disappeared_heals_at_next_snap(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """§5.7: the ``t=2`` for AAAA was lost in flight; AAAA still
        shows ``present``. The next periodic snap omits AAAA → server
        marks it gone and emits the missed event.
        """
        # SELECT current present epcs → [AAAA, BBBB]. Snap carries [BBBB].
        select_script = [["E2801160AAAA", "E2801160BBBB"]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [
                {"an": 1, "epc": "E2801160BBBB", "rssi": -50, "cnt": 1},
            ],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == []
        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["E2801160AAAA"]
        # BBBB was already present → no appeared event, but tag_reads still inserts.
        assert ingest.await_count == 1


# ---------------------------------------------------------------------------
# Large snap — soft cap warning, still processes
# ---------------------------------------------------------------------------


class TestLargeSnap:
    @pytest.mark.asyncio
    async def test_one_thousand_entries_processes_without_error(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """Spec §6 soft cap is 5000; 1000 entries is comfortably below
        but stresses the reconciler's collapse/upsert loop. All 1000
        EPCs are fresh → all 1000 appeared events; all 1000 tag_reads.
        """
        select_script: list[list[Any]] = [[]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        entries = [
            {"an": (i % 4) + 1, "epc": f"E2801160{i:08X}", "rssi": -50, "cnt": 1}
            for i in range(1000)
        ]
        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": entries,
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert len(_events(bus, Topic.SIGNALING_TAG_APPEARED)) == 1000
        assert _events(bus, Topic.SIGNALING_TAG_DISAPPEARED) == []
        assert ingest.await_count == 1000

    @pytest.mark.asyncio
    async def test_above_soft_cap_processes_with_warning(
        self,
        tenant_id: UUID,
        device_id: UUID,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Spec §6: ``epcs[]`` length above the 5000 soft cap is
        processed (NOT rejected) but a warning is logged. Phase E will
        wire the ``tagpulse_mqtt_wm_snap_large_total{sn}`` counter.
        """
        import logging

        select_script: list[list[Any]] = [[]]
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        # 5001 entries — one above the soft cap.
        entries = [{"an": 1, "epc": f"E280116{i:09X}", "rssi": -50, "cnt": 1} for i in range(5001)]
        body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": entries,
        }
        with caplog.at_level(logging.WARNING, logger="tagpulse.ingestion.mqtt_subscriber"):
            await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))

        assert any("above soft cap" in rec.message for rec in caplog.records)
        # Message is processed in full, not rejected.
        assert len(_events(bus, Topic.SIGNALING_TAG_APPEARED)) == 5001
        assert ingest.await_count == 5001
        sub._persist_mqtt_drop.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# v1/v2 coexistence on the same topic (spec §9.1 #4)
# ---------------------------------------------------------------------------


class TestV1V2Coexistence:
    @pytest.mark.asyncio
    async def test_v1_then_v2_on_same_subscriber(self, tenant_id: UUID, device_id: UUID) -> None:
        """A v1 dict-shaped payload (no ``t`` field) goes down the
        unchanged v1 path; an immediately-following v2 message goes
        through the v2 dispatch. The subscriber state stays clean
        across the transition.
        """
        select_script: list[list[Any]] = [[]]  # for the v2 snap's SELECT
        session = _FakeSession(select_results=select_script)
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)

        # v1 payload would normally go down the v1 path; we stub it out
        # so the test stays focused on dispatch routing (the v1 path is
        # covered by its own test suite). We confirm it never reaches
        # the v2 handler.
        v2_call_count = 0
        original_v2 = sub._handle_wm_v2_message

        async def counting_v2(*args: Any, **kwargs: Any) -> None:
            nonlocal v2_call_count
            v2_call_count += 1
            await original_v2(*args, **kwargs)

        sub._handle_wm_v2_message = counting_v2  # type: ignore[method-assign]

        # First: v1 message (no integer ``t``). The v1 path will try to
        # use the session — stub it via the same factory; we don't care
        # about the v1 side-effects here, only that v2 isn't called.
        v1_body = {"tag_id": "AABBCCDD", "timestamp": "2026-05-09T17:37:00Z"}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", v1_body))
        assert v2_call_count == 0

        # Then: v2 snap. v2 handler must run; events must emit.
        v2_body = {
            "t": 0,
            "sn": 123,
            "ts": TS_MS,
            "lat": None,
            "lon": None,
            "epcs": [{"an": 1, "epc": "E2801160AAAA", "rssi": -50, "cnt": 1}],
        }
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", v2_body))
        assert v2_call_count == 1
        assert _events(bus, Topic.SIGNALING_TAG_APPEARED) == ["E2801160AAAA"]

    @pytest.mark.asyncio
    async def test_string_t_field_does_not_trigger_v2_dispatch(
        self, tenant_id: UUID, device_id: UUID
    ) -> None:
        """Spec §9.1 #4 + dispatch hardening: a string-valued ``t``
        field on a v1 payload must not be confused for a v2 envelope.
        """
        session = _FakeSession()
        bus = _FakeBus()
        sub, ingest = _build_subscriber(session, bus)
        sub._handle_wm_v2_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("v2 dispatch must not run for string t")
        )

        body = {"t": "kind-not-int", "tag_id": "AABBCCDD"}
        await sub._handle_tag_read(tenant_id, device_id, _mqtt("t", body))
        # No exception was raised — the v2 guard correctly rejected the
        # string ``t`` field.
