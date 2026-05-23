"""Sprint 46 / ADR-025 — MQTT subscriber v2 wire-format dispatch.

Verifies that ``_handle_tag_read`` routes integer-``t`` payloads through
``_handle_wm_v2_message`` (spec §4.3) without disturbing v1 paths, and
that the v2 handler:

* invokes the right :mod:`presence_reconciler` coroutine,
* sets ``app.current_tenant_id`` on the session before any write,
* maps v2 fields to :class:`TagReadCreate` per spec §4.4 and pushes
  them through :class:`IngestionService.ingest`,
* drops invalid payloads through ``_persist_mqtt_drop`` with the
  spec §6 ``reason`` label, never propagating ``ValidationError``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tagpulse.ingestion.mqtt_subscriber import (
    MqttSubscriber,
    _classify_wm_validation_error,
)


def _make_subscriber() -> MqttSubscriber:
    return MqttSubscriber(
        host="broker.example",
        port=1883,
        session_factory=MagicMock(
            side_effect=AssertionError("session_factory must not be reached")
        ),
        event_bus=MagicMock(),
        usage_meter=None,
    )


def _msg(topic: str, payload: bytes | str) -> Any:
    return SimpleNamespace(
        topic=topic,
        payload=payload if isinstance(payload, bytes) else payload.encode(),
    )


def _wire_session(sub: MqttSubscriber) -> tuple[AsyncMock, AsyncMock]:
    """Replace the session factory + ingestion service with AsyncMocks.

    Returns ``(session, ingest_service)`` for assertions. The session is
    its own async context-manager.
    """
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    sub._session_factory = MagicMock(return_value=fake_session)  # type: ignore[method-assign]
    fake_ingest = SimpleNamespace(ingest=AsyncMock())
    sub._build_ingestion_service = MagicMock(return_value=fake_ingest)  # type: ignore[method-assign]
    sub._persist_mqtt_drop = AsyncMock()  # type: ignore[method-assign]
    return fake_session, fake_ingest


# ---------------------------------------------------------------------------
# Dispatch hook in _handle_tag_read
# ---------------------------------------------------------------------------


class TestV2Dispatch:
    @pytest.mark.asyncio
    async def test_integer_t_field_routes_to_v2_handler(self) -> None:
        sub = _make_subscriber()
        called: list[Any] = []

        async def fake_v2(tenant: Any, device: Any, raw: Any, message: Any) -> None:
            called.append(raw)

        sub._handle_wm_v2_message = fake_v2  # type: ignore[method-assign]

        body = json.dumps({"t": 0, "sn": 1, "ts": 1, "lat": None, "lon": None, "epcs": []})
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", body))

        assert len(called) == 1
        assert called[0]["t"] == 0

    @pytest.mark.asyncio
    async def test_string_t_field_takes_v1_path(self) -> None:
        """A string ``t`` field (or no ``t``) is NOT v2 — fall through."""
        sub = _make_subscriber()
        _wire_session(sub)
        sub._handle_wm_v2_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("v2 path must not run for non-int t")
        )

        # No ``t`` field at all — pure v1.
        body = json.dumps({"tag_id": "X", "timestamp": "2026-05-09T17:37:00Z"})
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", body))

        # String ``t`` — also not v2.
        body = json.dumps({"t": "not-an-int", "tag_id": "X", "timestamp": "2026-05-09T17:37:00Z"})
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", body))

    @pytest.mark.asyncio
    async def test_list_payload_takes_v1_path(self) -> None:
        sub = _make_subscriber()
        _wire_session(sub)
        sub._handle_wm_v2_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("v2 path must not run for list payloads")
        )

        body = json.dumps([{"tag_id": "X", "timestamp": "2026-05-09T17:37:00Z"}])
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", body))


# ---------------------------------------------------------------------------
# _handle_wm_v2_message — happy paths
# ---------------------------------------------------------------------------


class TestV2HandlerHappyPaths:
    @pytest.mark.asyncio
    async def test_snap_invokes_reconcile_and_ingest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sub = _make_subscriber()
        session, ingest = _wire_session(sub)

        reconcile = AsyncMock(return_value=(["AABBCCDD"], []))
        monkeypatch.setattr(
            "tagpulse.ingestion.mqtt_subscriber.presence_reconciler.reconcile_snap",
            reconcile,
        )

        tenant = uuid4()
        device = uuid4()
        body = {
            "t": 0,
            "sn": 1,
            "ts": 1_700_000_000_000,
            "lat": 47.6,
            "lon": -122.3,
            "epcs": [
                {"an": 1, "epc": "AABBCCDD", "rssi": -60, "cnt": 1},
                {"an": 2, "epc": "EEFF0011", "rssi": -55, "cnt": 1},
            ],
        }
        await sub._handle_tag_read(tenant, device, _msg("topic", json.dumps(body)))

        # RLS GUC + reconciler + commit must happen.
        reconcile.assert_awaited_once()
        session.commit.assert_awaited_once()
        # Two epcs → two ingest calls.
        assert ingest.ingest.await_count == 2
        # Verify first ingest carries spec §4.4 mapping fidelity.
        (called_tenant, called_read), _ = ingest.ingest.await_args_list[0]
        assert called_tenant == tenant
        assert called_read.device_id == device
        assert called_read.tag_id == "AABBCCDD"
        assert called_read.signal_strength == -60.0
        assert called_read.reader_antenna == 1
        assert called_read.identity is not None
        assert called_read.identity.epc_hex == "AABBCCDD"
        assert called_read.location is not None
        assert called_read.location.source == "reader_gnss"

    @pytest.mark.asyncio
    async def test_appeared_invokes_reconcile_and_one_ingest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sub = _make_subscriber()
        session, ingest = _wire_session(sub)

        apply_appeared = AsyncMock(return_value=True)
        monkeypatch.setattr(
            "tagpulse.ingestion.mqtt_subscriber.presence_reconciler.apply_appeared",
            apply_appeared,
        )

        body = {
            "t": 1,
            "sn": 1,
            "ts": 1_700_000_000_000,
            "lat": None,
            "lon": None,
            "an": 1,
            "epc": "AABBCCDD",
            "rssi": -60,
            "cnt": 1,
        }
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", json.dumps(body)))

        apply_appeared.assert_awaited_once()
        assert ingest.ingest.await_count == 1
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disappeared_invokes_reconcile_no_ingest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec §4.3: t=2 writes NO tag_reads row."""
        sub = _make_subscriber()
        session, ingest = _wire_session(sub)

        apply_disappeared = AsyncMock(return_value=True)
        monkeypatch.setattr(
            "tagpulse.ingestion.mqtt_subscriber.presence_reconciler.apply_disappeared",
            apply_disappeared,
        )

        body = {"t": 2, "sn": 1, "ts": 1_700_000_000_000, "epc": "AABBCCDD"}
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", json.dumps(body)))

        apply_disappeared.assert_awaited_once()
        assert ingest.ingest.await_count == 0
        session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# _handle_wm_v2_message — rejections (spec §6)
# ---------------------------------------------------------------------------


class TestV2HandlerRejections:
    @pytest.mark.asyncio
    async def test_unknown_t_value_drops_with_unknown_type_reason(self) -> None:
        sub = _make_subscriber()
        _wire_session(sub)
        # Session must NOT be opened for a rejection.
        sub._session_factory = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError("session must not open for invalid v2")
        )

        body = {"t": 99, "sn": 1, "ts": 1, "epc": "AABBCCDD"}
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", json.dumps(body)))

        sub._persist_mqtt_drop.assert_awaited_once()
        (_, _, _, kind, reason), _ = sub._persist_mqtt_drop.await_args
        assert kind == "wm_v2"
        assert reason == "unknown_type"

    @pytest.mark.asyncio
    async def test_invalid_epc_drops_with_invalid_epc_reason(self) -> None:
        sub = _make_subscriber()
        sub._session_factory = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError("session must not open for invalid v2")
        )
        sub._persist_mqtt_drop = AsyncMock()  # type: ignore[method-assign]

        # EPC "XYZ" — too short AND not hex.
        body = {"t": 2, "sn": 1, "ts": 1, "epc": "XYZ"}
        await sub._handle_tag_read(uuid4(), uuid4(), _msg("topic", json.dumps(body)))

        sub._persist_mqtt_drop.assert_awaited_once()
        (_, _, _, kind, reason), _ = sub._persist_mqtt_drop.await_args
        assert kind == "wm_v2"
        assert reason == "invalid_epc"


# ---------------------------------------------------------------------------
# _classify_wm_validation_error — direct unit coverage of edge cases
# ---------------------------------------------------------------------------


class TestClassifyValidationError:
    def _err(self, raw: dict[str, Any]) -> str:
        from pydantic import ValidationError

        from tagpulse.ingestion.mqtt_subscriber import _WM_MESSAGE_ADAPTER

        try:
            _WM_MESSAGE_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            return _classify_wm_validation_error(raw, exc)
        raise AssertionError("expected ValidationError")

    def test_missing_t_field(self) -> None:
        assert self._err({"sn": 1, "ts": 1, "epc": "AABBCCDD"}) == "missing_type"

    def test_unknown_t_field(self) -> None:
        assert self._err({"t": 7, "sn": 1, "ts": 1, "epc": "AABBCCDD"}) == "unknown_type"

    def test_invalid_epc_in_disappeared(self) -> None:
        assert self._err({"t": 2, "sn": 1, "ts": 1, "epc": "XYZ"}) == "invalid_epc"
