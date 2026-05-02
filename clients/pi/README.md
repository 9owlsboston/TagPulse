# TagPulse Edge Client

Reference Python implementation of the TagPulse **edge device contract** for
home-grown tag scanners and sensor gateways (RFID readers, with optional GPS,
temperature, and other on-board sensors).

The current reference target is a Raspberry Pi-class single-board computer —
hence the legacy `clients/pi/` path — but the code is hardware-agnostic and
is intended to run on any Linux/macOS host with Python 3.11+ that can talk
MQTT or HTTP. "Pi" is one experiment among many; the contract, schema, and
wire format do not assume a specific board.

This package is **not used by the TagPulse backend.** It is shipped to edge
device developers so every scanner / sensor device behaves the same way on
the wire. The design rationale and contract live in
[`docs/design/edge-device-contract.md`](../../docs/design/edge-device-contract.md)
and [`docs/design/asset-tracking-gap-analysis.md`](../../docs/design/asset-tracking-gap-analysis.md)
§4.A5.

---

## What it does

The agent sits between your reader hardware loop and the TagPulse MQTT broker
(or HTTP endpoint) and enforces:

- **De-duplication.** Identical `(tag_id, antenna)` reads inside a sliding
  window collapse to one event.
- **ENTER / EXIT semantics.** A tag emits one `ENTER` when it first appears
  and one `EXIT` after it has been silent for `exit_timeout_s`.
- **Batching.** Outgoing events are coalesced into one MQTT publish per
  second (or per N events, whichever first).
- **Offline buffer.** SQLite ring buffer with bounded size and max age;
  drained automatically on reconnect. Process restart safe.
- **Reconnect with backoff.** Full-jitter exponential backoff with a cap;
  the hardware loop never blocks on the network.
- **Time hygiene.** All timestamps are UTC. Events older than 24 h or more
  than 5 min in the future are dropped locally with a metric.
- **Heartbeat.** Status is published every 60 s with firmware version,
  uptime, and current buffer depth.

Everything is driven by an `EdgeConfig` dataclass so devices can be tuned via
their server-side `device.configuration` JSON without firmware rebuilds.

---

## Install on a device

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Requires Python 3.11+ and `paho-mqtt>=2.0`. Tested on Raspberry Pi OS, Debian,
Ubuntu, and macOS — any Linux distribution with a working `paho-mqtt` should
work.

## Minimal usage

```python
from datetime import UTC, datetime
from uuid import UUID

from tagpulse_edge import EdgeAgent, EdgeConfig, RawTagRead

config = EdgeConfig(
    tenant_id=UUID("..."),
    device_id=UUID("..."),
    broker_host="broker.example.com",
    broker_port=1883,
    username="device-...",
    password="<token>",
    buffer_path="/var/lib/tagpulse/edge.sqlite",
    firmware_version="edge-reader-0.1.0",
)

with EdgeAgent(config) as agent:
    # your reader hardware loop
    while True:
        tag_id, antenna, rssi = read_from_hardware()  # your code
        agent.submit_tag_read(
            RawTagRead(
                tag_id=tag_id,
                antenna=antenna,
                signal_strength=rssi,
                observed_at=datetime.now(UTC),
            )
        )
```

`EdgeAgent` also exposes `submit_telemetry(...)` for sensor-only metrics
(temperature, humidity, battery) and `submit_location(...)` for GPS fixes.

---

## Run the example

```bash
python -m examples.run_reader \
    --tenant-id 00000000-0000-0000-0000-000000000001 \
    --device-id 00000000-0000-0000-0000-000000000002 \
    --broker-host localhost
```

The example uses a fake hardware loop (random tags + temperature) and is
useful for soak-testing reconnect behavior.

---

## Tests

```bash
pip install -e '.[test]'
pytest
```

Pure-logic modules (`dedup`, `buffer`, `clock`) are fully unit-tested. The
MQTT transport has a fake-broker test that exercises reconnect/backoff.
