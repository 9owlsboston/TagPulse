"""Edge agent — wires hardware inputs to dedup -> outbox -> MQTT transport.

Threading model
---------------

The agent runs three background threads:

1. **tick** — calls ``PresenceTracker.tick()`` periodically to flush EXIT
   events for absent tags.
2. **publisher** — drains the SQLite outbox in batches whenever the MQTT
   transport reports connected.
3. **heartbeat** — publishes status every ``heartbeat_period_s``.

Hardware loops (your code) call ``submit_*`` from any thread; those calls
do the dedup/validation work *inline* and persist to the outbox. They never
do network I/O directly, so a slow / dead broker can never block the reader.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from tagpulse_edge.buffer import Outbox
from tagpulse_edge.clock import ClockGuard, to_utc
from tagpulse_edge.config import EdgeConfig
from tagpulse_edge.dedup import PresenceEvent, PresenceTracker, Transition
from tagpulse_edge.events import (
    LocationFix,
    OutboundEvent,
    RawTagRead,
    SensorSample,
)
from tagpulse_edge.transport import MqttTransport, Publisher

logger = logging.getLogger(__name__)


class EdgeAgent(AbstractContextManager["EdgeAgent"]):
    """Single-process edge agent. Use as a context manager or call start/stop."""

    def __init__(
        self,
        config: EdgeConfig,
        *,
        publisher: Publisher | None = None,
        outbox: Outbox | None = None,
    ) -> None:
        logging.basicConfig(
            level=getattr(logging, config.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        self._config = config
        self._tracker = PresenceTracker(
            dedup_window_s=config.dedup_window_s,
            exit_timeout_s=config.exit_timeout_s,
        )
        self._clock = ClockGuard(
            max_age_s=config.max_event_age_s,
            max_skew_future_s=config.max_event_skew_future_s,
        )
        self._outbox = outbox or Outbox(
            config.buffer_path,
            max_rows=config.buffer_max_rows,
            max_age_s=config.buffer_max_age_s,
        )
        self._publisher: Publisher = publisher or MqttTransport(
            config,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )

        self._stop = threading.Event()
        self._connected = threading.Event()
        self._wake_publisher = threading.Event()

        self._started_mono = time.monotonic()
        self._dropped_old = 0
        self._dropped_future = 0

        self._threads: list[threading.Thread] = []

    # -- Lifecycle --

    def start(self) -> None:
        self._stop.clear()
        self._publisher.start()
        for target, name in (
            (self._tick_loop, "edge-tick"),
            (self._publisher_loop, "edge-publisher"),
            (self._heartbeat_loop, "edge-heartbeat"),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        logger.info("EdgeAgent started for device %s", self._config.device_id)

    def stop(self) -> None:
        logger.info("EdgeAgent stopping")
        self._stop.set()
        self._wake_publisher.set()
        # Best-effort offline LWT-equivalent on clean shutdown.
        try:
            self._publish_status(connection_state="offline", reason="shutdown")
        except Exception:  # noqa: BLE001
            pass
        self._publisher.stop()
        for t in self._threads:
            t.join(timeout=2.0)
        self._outbox.close()

    def __enter__(self) -> EdgeAgent:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # -- Hardware loop API --

    def submit_tag_read(self, read: RawTagRead) -> None:
        """Feed one raw read in. Dedup + ENTER decision happens here."""
        if not self._validate_ts(read.observed_at):
            return
        mono = time.monotonic()
        enter = self._tracker.observe(
            tag_id=read.tag_id,
            antenna=read.antenna,
            monotonic_s=mono,
            signal_strength=read.signal_strength,
        )
        if enter is not None:
            self._enqueue_presence(
                enter,
                sensor_data=read.sensor_data,
                observed_at=read.observed_at,
                identity={
                    "epc": read.epc,
                    "epc_hex": read.epc_hex,
                    "tid": read.tid,
                    "user_memory_hex": read.user_memory_hex,
                },
                tag_data=read.tag_data,
                reader_antenna=read.reader_antenna,
            )

    def submit_telemetry(self, sample: SensorSample) -> None:
        if not self._validate_ts(sample.observed_at):
            return
        ts = to_utc(sample.observed_at)
        payload: dict[str, Any] = {
            "device_id": str(self._config.device_id),
            "metric_name": sample.metric_name,
            "metric_value": sample.value,
            "timestamp": ts.isoformat(),
        }
        if sample.unit is not None:
            payload["unit"] = sample.unit
        if sample.metadata is not None:
            payload["metadata"] = sample.metadata
        self._enqueue("telemetry", payload)

    def submit_location(self, fix: LocationFix) -> None:
        if not self._validate_ts(fix.observed_at):
            return
        ts = to_utc(fix.observed_at)
        payload = {
            "device_id": str(self._config.device_id),
            "latitude": fix.latitude,
            "longitude": fix.longitude,
            "accuracy_m": fix.accuracy_m,
            "source": fix.source,
            "timestamp": ts.isoformat(),
        }
        self._enqueue("location", payload)

    # -- Background loops --

    def _tick_loop(self) -> None:
        period = max(0.25, self._config.exit_timeout_s / 4)
        while not self._stop.is_set():
            mono = time.monotonic()
            for ev in self._tracker.tick(mono):
                self._enqueue_presence(ev)
            self._stop.wait(timeout=period)

    def _publisher_loop(self) -> None:
        """Drain the outbox in batches when connected."""
        while not self._stop.is_set():
            if not self._connected.is_set():
                # Wait for connect or stop.
                self._wake_publisher.wait(timeout=1.0)
                self._wake_publisher.clear()
                continue

            batch = self._outbox.peek(self._config.batch_max_events)
            if not batch:
                # Nothing to send — sleep up to batch_max_age_s.
                self._wake_publisher.wait(timeout=self._config.batch_max_age_s)
                self._wake_publisher.clear()
                continue

            acked: list[int] = []
            for event in batch:
                payload = json.dumps(event.payload, separators=(",", ":")).encode("utf-8")
                if not self._publisher.publish(event.topic, payload, qos=1):
                    # Lost connection mid-batch; bail and let the loop reconnect.
                    break
                if event.rowid is not None:
                    acked.append(event.rowid)
            if acked:
                self._outbox.ack(acked)

    def _heartbeat_loop(self) -> None:
        # Send first heartbeat as soon as we connect; thereafter on schedule.
        while not self._stop.is_set():
            if self._connected.is_set():
                self._publish_status(connection_state="online")
            self._stop.wait(timeout=self._config.heartbeat_period_s)

    # -- Helpers --

    def _validate_ts(self, ts: datetime | None) -> bool:
        if ts is None:
            return True
        if not self._clock.is_acceptable(ts):
            now = datetime.now(UTC)
            if to_utc(ts) > now:
                self._dropped_future += 1
                logger.warning("Dropping event in the future: %s (now=%s)", ts, now)
            else:
                self._dropped_old += 1
                logger.warning("Dropping stale event: %s (now=%s)", ts, now)
            return False
        return True

    def _enqueue_presence(
        self,
        ev: PresenceEvent,
        *,
        sensor_data: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        identity: dict[str, Any] | None = None,
        tag_data: dict[str, Any] | None = None,
        reader_antenna: int | None = None,
    ) -> None:
        ts = to_utc(observed_at)
        payload: dict[str, Any] = {
            "device_id": str(self._config.device_id),
            "tag_id": ev.tag_id,
            "antenna": ev.antenna,
            "event_type": ev.transition.value,
            "timestamp": ts.isoformat(),
        }
        if ev.signal_strength is not None:
            payload["signal_strength"] = ev.signal_strength
        if sensor_data is not None and ev.transition is Transition.ENTER:
            payload["sensor_data"] = sensor_data
        if identity:
            stripped = {k: v for k, v in identity.items() if v is not None}
            if stripped:
                payload["identity"] = stripped
        if tag_data is not None and ev.transition is Transition.ENTER:
            payload["tag_data"] = tag_data
        if reader_antenna is not None:
            payload["reader_antenna"] = reader_antenna
        self._enqueue("tag-reads", payload)

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        topic = self._config.topic(kind)
        event = OutboundEvent(
            kind=kind,  # type: ignore[arg-type]
            topic=topic,
            payload=payload,
            enqueued_at=time.monotonic(),
        )
        self._outbox.put(event)
        if self._connected.is_set():
            self._wake_publisher.set()

    def _publish_status(self, *, connection_state: str, reason: str | None = None) -> None:
        payload = {
            "connection_state": connection_state,
            "firmware_version": self._config.firmware_version,
            "uptime_s": int(time.monotonic() - self._started_mono),
            "buffer_depth": self._outbox.depth(),
            "tags_present": self._tracker.present_count(),
            "dropped_old": self._dropped_old,
            "dropped_future": self._dropped_future,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if reason is not None:
            payload["reason"] = reason
        # Status is small and important — publish directly when possible.
        if self._publisher.is_connected():
            self._publisher.publish(
                self._config.topic("status"),
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                qos=1,
            )
        else:
            # Otherwise enqueue so it's delivered after reconnect.
            self._enqueue("status", payload)

    # -- Transport callbacks --

    def _on_connect(self) -> None:
        self._connected.set()
        self._wake_publisher.set()

    def _on_disconnect(self) -> None:
        self._connected.clear()
