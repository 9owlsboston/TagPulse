"""Integration test for the agent using a fake publisher."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tagpulse_edge import EdgeAgent, EdgeConfig, RawTagRead, SensorSample


class FakePublisher:
    """In-memory publisher that records published topics+payloads."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self._connected = threading.Event()
        self._connected.set()
        self.on_connect_cb = None
        self.on_disconnect_cb = None

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> bool:
        self.published.append((topic, payload))
        return True

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _config(tmp_path: Path) -> EdgeConfig:
    return EdgeConfig(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        device_id=UUID("00000000-0000-0000-0000-000000000002"),
        buffer_path=str(tmp_path / "edge.sqlite"),
        dedup_window_s=10.0,
        exit_timeout_s=10.0,
        batch_max_age_s=0.1,
        heartbeat_period_s=3600.0,  # don't fire during the test
    )


def test_tag_read_round_trip(tmp_path: Path) -> None:
    fake = FakePublisher()
    config = _config(tmp_path)
    agent = EdgeAgent(config, publisher=fake)
    agent._connected.set()  # bypass real transport handshake
    agent.start()
    try:
        agent.submit_tag_read(
            RawTagRead(
                tag_id="TAG1",
                antenna="ant-1",
                signal_strength=-42.0,
                observed_at=datetime.now(UTC),
            )
        )
        # Duplicate within dedup window — should be suppressed.
        agent.submit_tag_read(
            RawTagRead(tag_id="TAG1", antenna="ant-1", observed_at=datetime.now(UTC))
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            t.endswith("/tag-reads") for t, _ in fake.published
        ):
            time.sleep(0.05)
    finally:
        agent.stop()

    tag_publishes = [p for p in fake.published if p[0].endswith("/tag-reads")]
    assert len(tag_publishes) == 1
    assert b'"event_type":"ENTER"' in tag_publishes[0][1]


def test_telemetry_round_trip(tmp_path: Path) -> None:
    fake = FakePublisher()
    agent = EdgeAgent(_config(tmp_path), publisher=fake)
    agent._connected.set()
    agent.start()
    try:
        agent.submit_telemetry(
            SensorSample(
                metric_name="temperature",
                value=21.5,
                unit="C",
                observed_at=datetime.now(UTC),
            )
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            t.endswith("/telemetry") for t, _ in fake.published
        ):
            time.sleep(0.05)
    finally:
        agent.stop()

    tel = [p for p in fake.published if p[0].endswith("/telemetry")]
    assert len(tel) == 1
    assert b'"metric_name":"temperature"' in tel[0][1]


def test_buffers_when_disconnected_then_drains(tmp_path: Path) -> None:
    fake = FakePublisher()
    agent = EdgeAgent(_config(tmp_path), publisher=fake)
    # Start disconnected.
    agent._connected.clear()
    agent.start()
    try:
        for i in range(3):
            agent.submit_tag_read(
                RawTagRead(tag_id=f"T{i}", antenna="a", observed_at=datetime.now(UTC))
            )
        # Nothing published yet.
        time.sleep(0.3)
        assert fake.published == []
        # Now "reconnect".
        agent._on_connect()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(fake.published) < 3:
            time.sleep(0.05)
    finally:
        agent.stop()

    assert len([p for p in fake.published if p[0].endswith("/tag-reads")]) == 3
