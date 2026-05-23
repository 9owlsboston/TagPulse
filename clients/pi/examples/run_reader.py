"""Example: simulated reader hardware loop driving the edge agent.

Run with:
    python -m examples.run_reader \
        --tenant-id <UUID> --device-id <UUID> --broker-host localhost
"""

from __future__ import annotations

import argparse
import logging
import random
import signal
import time
from datetime import UTC, datetime
from uuid import UUID

from tagpulse_edge import EdgeAgent, EdgeConfig, LocationFix, RawTagRead, SensorSample

TAG_POOL = [f"TAG{i:04d}" for i in range(1, 21)]
ANTENNAS = ["ant-1", "ant-2"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated TagPulse Pi reader")
    parser.add_argument("--tenant-id", required=True, type=UUID)
    parser.add_argument("--device-id", required=True, type=UUID)
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", default=1883, type=int)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--buffer-path", default="/tmp/tagpulse-edge.sqlite")  # noqa: S108 # demo simulator default; override for prod
    parser.add_argument("--read-interval-s", default=0.5, type=float)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = EdgeConfig(
        tenant_id=args.tenant_id,
        device_id=args.device_id,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        username=args.username,
        password=args.password,
        buffer_path=args.buffer_path,
        firmware_version="example-reader-0.1.0",
        # Tighter timings for demo visibility:
        dedup_window_s=2.0,
        exit_timeout_s=5.0,
        heartbeat_period_s=15.0,
    )

    stop = False

    def _shutdown(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    with EdgeAgent(config) as agent:
        last_telemetry = 0.0
        last_location = 0.0
        while not stop:
            agent.submit_tag_read(
                RawTagRead(
                    tag_id=random.choice(TAG_POOL),  # noqa: S311 # demo simulator
                    antenna=random.choice(ANTENNAS),  # noqa: S311 # demo simulator
                    signal_strength=round(random.uniform(-80, -30), 1),  # noqa: S311 # demo simulator
                    observed_at=datetime.now(UTC),
                )
            )
            now = time.monotonic()
            if now - last_telemetry > 5.0:
                agent.submit_telemetry(
                    SensorSample(
                        metric_name="temperature",
                        value=round(random.uniform(18.0, 28.0), 2),  # noqa: S311 # demo simulator
                        unit="C",
                        observed_at=datetime.now(UTC),
                    )
                )
                last_telemetry = now
            if now - last_location > 10.0:
                agent.submit_location(
                    LocationFix(
                        latitude=42.36 + random.uniform(-0.001, 0.001),  # noqa: S311 # demo simulator
                        longitude=-71.06 + random.uniform(-0.001, 0.001),  # noqa: S311 # demo simulator
                        accuracy_m=5.0,
                        observed_at=datetime.now(UTC),
                    )
                )
                last_location = now
            time.sleep(args.read_interval_s)


if __name__ == "__main__":
    main()
