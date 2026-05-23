"""Sprint 16 §8 — TokenRevokedError surface from the MQTT transport.

The agent embedder receives `on_token_revoked` callbacks when the broker
rejects credentials with an MQTT v5 NotAuthorized reason code (4 / 5 / 134 /
135), so it can swap in a freshly rotated token without restarting.
"""

from __future__ import annotations

from unittest.mock import patch

from tagpulse_edge.config import EdgeConfig
from tagpulse_edge.transport import (
    MqttTransport,
    TokenRevokedError,
    _reason_is_not_authorized,
)


def _config() -> EdgeConfig:
    return EdgeConfig(
        device_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        broker_host="localhost",
        broker_port=1883,
        username="u",
        password="p",  # noqa: S106 # test fixture
    )


class TestReasonCodeClassification:
    def test_zero_not_revoked(self) -> None:
        assert _reason_is_not_authorized(0) is False

    def test_135_revoked(self) -> None:
        assert _reason_is_not_authorized(135) is True

    def test_object_with_value_135(self) -> None:
        class _RC:
            value = 135

        assert _reason_is_not_authorized(_RC()) is True

    def test_garbage_not_revoked(self) -> None:
        assert _reason_is_not_authorized("nope") is False


class TestTokenRevokedSurfacedOnConnect:
    def test_callback_invoked_on_not_authorized_connect(self) -> None:
        with patch("tagpulse_edge.transport.mqtt"):
            received: list[TokenRevokedError] = []
            t = MqttTransport(_config(), on_token_revoked=received.append)
            t._handle_connect(None, None, None, 135, None)
        assert len(received) == 1
        assert isinstance(received[0], TokenRevokedError)


class TestTokenRevokedSurfacedOnDisconnect:
    def test_callback_invoked_on_not_authorized_disconnect(self) -> None:
        with patch("tagpulse_edge.transport.mqtt"):
            received: list[TokenRevokedError] = []
            t = MqttTransport(_config(), on_token_revoked=received.append)
            # Mark connected so disconnect path runs through both branches.
            t._connected.set()
            t._handle_disconnect(None, None, None, 135, None)
        assert len(received) == 1
        assert isinstance(received[0], TokenRevokedError)

    def test_normal_disconnect_no_callback(self) -> None:
        with patch("tagpulse_edge.transport.mqtt"):
            received: list[TokenRevokedError] = []
            t = MqttTransport(_config(), on_token_revoked=received.append)
            t._connected.set()
            t._handle_disconnect(None, None, None, 0, None)
        assert received == []
