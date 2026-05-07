"""MQTT transport with reconnect, backoff, and an injectable publisher.

Why a thin wrapper over paho-mqtt?

- We need full-jitter exponential backoff (paho's built-in reconnect is
  fixed-interval).
- We need a way to *inject* a fake publisher in tests.
- We need clear ``connected``/``disconnected`` callbacks so the agent can
  flip between buffering and draining states.
"""

from __future__ import annotations

import logging
import random
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import paho.mqtt.client as mqtt

from tagpulse_edge.config import EdgeConfig

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    """Anything we can publish bytes through. Tests use a fake."""

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> bool: ...
    def is_connected(self) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


class TokenRevokedError(Exception):
    """Raised/surfaced when the broker rejects auth (MQTT v5 reason code 135).

    Per docs/design/edge-device-contract.md §8, the agent should swap in a new
    token (delivered out-of-band) and reconnect rather than retrying the same
    revoked credential indefinitely.
    """


# MQTT v5 reason codes that mean "the token is no longer valid". paho exposes
# integer codes via ReasonCode.value; we accept either a bare int or an object
# with `.value`.
_MQTT_NOT_AUTHORIZED_CODES = frozenset({4, 5, 134, 135})


def _reason_is_not_authorized(reason_code: Any) -> bool:
    code = getattr(reason_code, "value", reason_code)
    try:
        return int(code) in _MQTT_NOT_AUTHORIZED_CODES
    except (TypeError, ValueError):
        return False


class MqttTransport:
    """paho-mqtt wrapper with full-jitter exponential backoff."""

    def __init__(
        self,
        config: EdgeConfig,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_token_revoked: Callable[[TokenRevokedError], None] | None = None,
    ) -> None:
        self._config = config
        self._on_connect_cb = on_connect or (lambda: None)
        self._on_disconnect_cb = on_disconnect or (lambda: None)
        self._on_token_revoked_cb = on_token_revoked or (lambda _exc: None)

        self._connected = threading.Event()
        self._stop = threading.Event()
        self._reconnect_thread: threading.Thread | None = None

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"tagpulse-edge-{config.device_id}",
            clean_session=False,
        )
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        if config.use_tls:
            self._client.tls_set(
                ca_certs=config.tls_ca_path,
                certfile=config.tls_cert_path,
                keyfile=config.tls_key_path,
                cert_reqs=ssl.CERT_REQUIRED,
            )
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect

        # Last Will: broker tells the world we died.
        will_topic = config.topic("status")
        self._client.will_set(
            will_topic,
            payload=b'{"connection_state":"offline","reason":"lwt"}',
            qos=1,
            retain=True,
        )

    # -- Lifecycle --

    def start(self) -> None:
        self._stop.clear()
        self._client.loop_start()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, name="edge-mqtt-reconnect", daemon=True
        )
        self._reconnect_thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 — paho can raise during shutdown
            pass
        self._client.loop_stop()
        if self._reconnect_thread:
            self._reconnect_thread.join(timeout=2.0)

    # -- Public API --

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> bool:
        if not self._connected.is_set():
            return False
        info = self._client.publish(topic, payload, qos=qos)
        # qos=1 returns immediately; we only need to know it was queued.
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # -- Internals --

    def _handle_connect(self, _client: Any, _ud: Any, _flags: Any, reason_code: Any, _props: Any) -> None:
        if _reason_is_not_authorized(reason_code):
            err = TokenRevokedError(
                f"broker rejected credentials (reason={reason_code})"
            )
            logger.error(
                "Device token appears revoked; awaiting reload — %s", err
            )
            self._on_token_revoked_cb(err)
            return
        if reason_code == 0 or getattr(reason_code, "is_failure", False) is False:
            logger.info("MQTT connected to %s:%d", self._config.broker_host, self._config.broker_port)
            self._connected.set()
            self._on_connect_cb()
        else:
            logger.warning("MQTT connect failed: %s", reason_code)

    def _handle_disconnect(self, _client: Any, _ud: Any, _flags: Any, reason_code: Any, _props: Any) -> None:
        was_connected = self._connected.is_set()
        self._connected.clear()
        if was_connected:
            logger.warning("MQTT disconnected: %s", reason_code)
            self._on_disconnect_cb()
        if _reason_is_not_authorized(reason_code):
            err = TokenRevokedError(
                f"broker forced disconnect (reason={reason_code})"
            )
            logger.error(
                "Device token appears revoked; awaiting reload — %s", err
            )
            self._on_token_revoked_cb(err)

    def _reconnect_loop(self) -> None:
        """Drive (re)connection with full-jitter exponential backoff."""
        attempt = 0
        while not self._stop.is_set():
            if self._connected.is_set():
                time.sleep(0.5)
                continue
            try:
                self._client.connect(
                    self._config.broker_host,
                    self._config.broker_port,
                    keepalive=self._config.keepalive_s,
                )
                # Wait briefly for the on_connect callback.
                if self._connected.wait(timeout=5.0):
                    attempt = 0
                    continue
            except Exception as exc:  # noqa: BLE001 — network errors are expected
                logger.info("MQTT connect attempt %d failed: %s", attempt + 1, exc)

            attempt += 1
            delay = _full_jitter(
                attempt,
                base=self._config.reconnect_initial_s,
                cap=self._config.reconnect_max_s,
            )
            logger.info("MQTT reconnect in %.1fs (attempt %d)", delay, attempt)
            self._stop.wait(timeout=delay)


def _full_jitter(attempt: int, *, base: float, cap: float) -> float:
    """AWS-style full-jitter backoff."""
    expo = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0, expo)
