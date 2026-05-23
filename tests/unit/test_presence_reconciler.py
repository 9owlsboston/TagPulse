"""Unit tests for the v2 wire-format presence reconciler (Sprint 46 / ADR-026)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from tagpulse.events.protocol import Event, Topic
from tagpulse.ingestion import presence_reconciler
from tagpulse.ingestion.wm_wire_format import (
    WmAppearedMessage,
    WmDisappearedMessage,
    WmSnapEntry,
    WmSnapMessage,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[Topic, Event]] = []

    async def publish(self, topic: Topic, event: Event) -> None:
        self.published.append((topic, event))


class _ScalarResult:
    """Stand-in for ``await session.execute(select(...))`` results.

    Supports both ``.all()`` (returns ``[(value,), ...]``) and
    ``.scalar_one_or_none()`` (returns the first value or ``None``).
    """

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any]]:
        return [(r,) for r in self._rows]

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records every ``execute`` call; SELECTs return scripted results.

    Match logic: walks ``select_results`` in order; every other call
    (insert/update) returns a no-op result. Keeps tests focused on
    event emission rather than precise SQL.
    """

    def __init__(self, select_results: list[list[Any]] | None = None) -> None:
        self.executed: list[Any] = []
        self.params: list[Any] = []
        self._selects = list(select_results or [])

    async def execute(self, stmt: Any, params: Any | None = None) -> Any:
        self.executed.append(stmt)
        self.params.append(params)
        kind = type(stmt).__name__
        # Plain SELECT statements come back as ``Select``; pg upserts as
        # ``Insert``; updates as ``Update``. We script SELECTs only.
        if kind == "Select":
            if self._selects:
                return _ScalarResult(self._selects.pop(0))
            return _ScalarResult([])
        return _ScalarResult([])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


TENANT = uuid4()
DEVICE = uuid4()
TS_MS = 1_700_000_000_000  # arbitrary epoch ms
TS_DT = datetime.fromtimestamp(TS_MS / 1000.0, tz=UTC)


def _entry(epc: str, *, rssi: int = -60, an: int = 1, **extra: Any) -> WmSnapEntry:
    return WmSnapEntry(epc=epc, rssi=rssi, an=an, cnt=1, **extra)


def _snap(epcs: list[WmSnapEntry], *, sn: int = 1) -> WmSnapMessage:
    return WmSnapMessage(t=0, sn=sn, ts=TS_MS, lat=None, lon=None, epcs=epcs)


def _payload_epcs(bus: _FakeBus, topic: Topic) -> list[str]:
    return [ev.payload["epc"] for t, ev in bus.published if t == topic]


# ---------------------------------------------------------------------------
# reconcile_snap (spec §4.2, §5.2 / §5.3 / §5.4 / §5.5 / §5.7)
# ---------------------------------------------------------------------------


class TestReconcileSnap:
    @pytest.mark.asyncio
    async def test_first_snap_emits_appeared_for_every_epc(self) -> None:
        """§5.2: empty present-set → every snap entry is appeared."""
        session = _FakeSession(select_results=[[]])  # no rows present
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD"), _entry("CCDDEEFF"), _entry("EEFF1122")])

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == ["AABBCCDD", "CCDDEEFF", "EEFF1122"]
        assert gone == []
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_APPEARED) == [
            "AABBCCDD",
            "CCDDEEFF",
            "EEFF1122",
        ]
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_DISAPPEARED) == []

    @pytest.mark.asyncio
    async def test_idempotent_snap_emits_nothing(self) -> None:
        """§5.3: present-set == snap-set → no events."""
        session = _FakeSession(select_results=[["AABBCCDD", "CCDDEEFF"]])
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD"), _entry("CCDDEEFF")])

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == []
        assert gone == []
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_missing_from_snap_marked_gone(self) -> None:
        """§5.3: EPC in present-set but not in snap → gone + event."""
        session = _FakeSession(select_results=[["AABBCCDD", "CCDDEEFF", "EEFF1122"]])
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD"), _entry("CCDDEEFF")])  # EEFF dropped

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == []
        assert gone == ["EEFF1122"]
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["EEFF1122"]

    @pytest.mark.asyncio
    async def test_empty_snap_marks_all_gone(self) -> None:
        """§5.4: empty epcs[] → every currently-present EPC goes gone."""
        session = _FakeSession(select_results=[["AABBCCDD", "CCDDEEFF"]])
        bus = _FakeBus()
        msg = _snap([])

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == []
        assert gone == ["AABBCCDD", "CCDDEEFF"]
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["AABBCCDD", "CCDDEEFF"]

    @pytest.mark.asyncio
    async def test_snap_after_reboot_emits_appeared_for_known_epcs(self) -> None:
        """§5.5: reader rejoins, snap == prior present-set → no events.

        With sticky present-state in the table, an unchanged snap
        across a reconnect produces zero transitions. This is the
        self-healing property promised by ADR-026.
        """
        session = _FakeSession(select_results=[["AABBCCDD"]])
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD")])

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == []
        assert gone == []
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_multi_antenna_collapses_to_one_event(self) -> None:
        """§9: same EPC on multiple antennas collapses to one row/event."""
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        msg = _snap(
            [
                _entry("AABBCCDD", rssi=-70, an=1),
                _entry("AABBCCDD", rssi=-55, an=2),  # winner
                _entry("AABBCCDD", rssi=-65, an=3),
            ]
        )

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == ["AABBCCDD"]
        # One event, not three.
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_APPEARED) == ["AABBCCDD"]

    @pytest.mark.asyncio
    async def test_event_payload_has_required_fields(self) -> None:
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD")])

        await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        topic, event = bus.published[0]
        assert topic is Topic.SIGNALING_TAG_APPEARED
        assert event.topic is Topic.SIGNALING_TAG_APPEARED
        assert event.timestamp == TS_DT
        assert event.payload == {
            "tenant_id": str(TENANT),
            "device_id": str(DEVICE),
            "epc": "AABBCCDD",
            "observed_at": TS_DT.isoformat(),
            "source": "snap",
        }


# ---------------------------------------------------------------------------
# apply_appeared (t=1)
# ---------------------------------------------------------------------------


class TestApplyAppeared:
    @pytest.mark.asyncio
    async def test_new_epc_emits_event(self) -> None:
        session = _FakeSession(select_results=[[]])  # never seen
        bus = _FakeBus()
        msg = WmAppearedMessage(
            t=1, sn=1, ts=TS_MS, lat=None, lon=None, an=1, epc="AABBCCDD", rssi=-60, cnt=1
        )

        emitted = await presence_reconciler.apply_appeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is True
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_APPEARED) == ["AABBCCDD"]
        assert bus.published[0][1].payload["source"] == "delta"

    @pytest.mark.asyncio
    async def test_present_to_present_is_noop(self) -> None:
        """Re-appeared signal for already-present EPC → upsert, no event."""
        session = _FakeSession(select_results=[["present"]])
        bus = _FakeBus()
        msg = WmAppearedMessage(
            t=1, sn=1, ts=TS_MS, lat=None, lon=None, an=1, epc="AABBCCDD", rssi=-60, cnt=1
        )

        emitted = await presence_reconciler.apply_appeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is False
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_gone_to_present_emits_event(self) -> None:
        session = _FakeSession(select_results=[["gone"]])
        bus = _FakeBus()
        msg = WmAppearedMessage(
            t=1, sn=1, ts=TS_MS, lat=None, lon=None, an=1, epc="AABBCCDD", rssi=-60, cnt=1
        )

        emitted = await presence_reconciler.apply_appeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is True
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_APPEARED) == ["AABBCCDD"]


# ---------------------------------------------------------------------------
# apply_disappeared (t=2)
# ---------------------------------------------------------------------------


class TestApplyDisappeared:
    @pytest.mark.asyncio
    async def test_present_to_gone_emits_event(self) -> None:
        session = _FakeSession(select_results=[["present"]])
        bus = _FakeBus()
        msg = WmDisappearedMessage(t=2, sn=1, ts=TS_MS, epc="AABBCCDD")

        emitted = await presence_reconciler.apply_disappeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is True
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["AABBCCDD"]
        assert bus.published[0][1].payload["source"] == "delta"

    @pytest.mark.asyncio
    async def test_unknown_epc_silently_ignored(self) -> None:
        """Spec §6: t=2 for never-seen EPC → log, no event, no error."""
        session = _FakeSession(select_results=[[]])
        bus = _FakeBus()
        msg = WmDisappearedMessage(t=2, sn=1, ts=TS_MS, epc="AABBCCDD")

        emitted = await presence_reconciler.apply_disappeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is False
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_already_gone_is_idempotent(self) -> None:
        session = _FakeSession(select_results=[["gone"]])
        bus = _FakeBus()
        msg = WmDisappearedMessage(t=2, sn=1, ts=TS_MS, epc="AABBCCDD")

        emitted = await presence_reconciler.apply_disappeared(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert emitted is False
        assert bus.published == []


# ---------------------------------------------------------------------------
# §5.7 — lost t=2 healed by next snap (composite scenario)
# ---------------------------------------------------------------------------


class TestLostDeltaHealedBySnap:
    @pytest.mark.asyncio
    async def test_snap_completes_missing_disappearance(self) -> None:
        """EPC currently present, never received a t=2, then arrives in
        a snap that omits it → reconciliation emits the missed
        ``disappeared`` event without operator intervention."""
        # present: AABB, CCDD. Snap drops CCDD.
        session = _FakeSession(select_results=[["AABBCCDD", "CCDDEEFF"]])
        bus = _FakeBus()
        msg = _snap([_entry("AABBCCDD")])

        appeared, gone = await presence_reconciler.reconcile_snap(
            session, bus, tenant_id=TENANT, device_id=DEVICE, msg=msg
        )

        assert appeared == []
        assert gone == ["CCDDEEFF"]
        assert _payload_epcs(bus, Topic.SIGNALING_TAG_DISAPPEARED) == ["CCDDEEFF"]
