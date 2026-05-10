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

## Smoke-publish from the CLI

For ad-hoc broker sanity checks (no `EdgeAgent` machinery, just one MQTT
publish per invocation), use the standalone publisher:

> **Prerequisites.** The publisher is a single-file script that talks to the
> broker directly via [`paho-mqtt`](https://pypi.org/project/paho-mqtt/) — it
> does **not** import `tagpulse_edge`, so you don't need `pip install -e .`
> for it. Either install the dep into any Python 3.11+ env
> (`pip install 'paho-mqtt>=2.0'`) or just reuse the edge-client venv from
> the [Install on a device](#install-on-a-device) section above, which
> already pulls it in.

```bash
# one tag-read with sensor + GPS + EPC
python3 examples/paho_smoke_publisher.py --once \
  --lat 42.36 --lon -71.06 --temp-c 21.4 --battery 88

# standalone GPS fix on the device's location topic
python3 examples/paho_smoke_publisher.py --once --topic location \
  --lat 42.36 --lon -71.06 --accuracy 6

# device-side event (e.g. heartbeat)
python3 examples/paho_smoke_publisher.py --once --topic events \
  --event-type heartbeat --detail uptime_s=1234
```

Configuration (broker host, tenant, device, password) is read from
`.tp_paho_edge.env` next to the script — copy `.tp_paho_edge.env.example`
and fill it in. CLI flags and real env vars override the file.

### Simulating device movement

Two ways to feed a sequence of GPS waypoints to the publisher; pick based on
how realistic the movement needs to be.

**Option A — shell driver, one MQTT connection per point.** Good for
slow-moving devices (≤ 1 publish every few seconds) and demos. Every row
opens a fresh CONNACK, so don't expect >0.5 Hz.

```bash
./examples/drive_track.sh examples/tracks/boston-loop.csv
```

**Option B — built-in `--track` flag, single MQTT session.** Required for
realistic GPS rates (1 Hz and up). Optional `--track-interp HZ` linearly
interpolates intermediate points between waypoints so a sparse address list
becomes a smooth track:

```bash
# walk the track once at the per-row dwell_s pacing
python3 examples/paho_smoke_publisher.py --topic location \
  --track examples/tracks/boston-loop.csv

# smooth 1 Hz GPS, repeat until SIGINT
python3 examples/paho_smoke_publisher.py --topic location \
  --track examples/tracks/boston-loop.csv \
  --track-interp 1 --track-loop
```

Track files are CSV with columns `lat,lon[,accuracy_m,dwell_s]`. A header
row and `#`-prefixed comments are allowed. `dwell_s` is the time the
device stays at that point before moving on (used directly in Option A and
to size each interpolated segment in Option B). Pre-compute waypoints
offline (geocode street addresses, export from a routing engine, etc.) and
commit the CSV under `examples/tracks/` for repeatable smoke tests.

Sample tracks bundled in [`examples/tracks/`](examples/tracks/):

| File | Loop |
| ---- | ---- |
| [`boston-loop.csv`](examples/tracks/boston-loop.csv) | Downtown Boston (Common → Faneuil Hall → Back Bay) |
| [`sfo-loop.csv`](examples/tracks/sfo-loop.csv) | San Francisco (Union Square → Financial District → Embarcadero) |
| [`chicago-loop.csv`](examples/tracks/chicago-loop.csv) | Chicago Loop (Millennium Park → Magnificent Mile → Art Institute) |
| [`la-loop.csv`](examples/tracks/la-loop.csv) | Downtown LA (Disney Hall → Pershing Square → City Hall → Union Station) |
| [`dc-loop.csv`](examples/tracks/dc-loop.csv) | National Mall (Lincoln Memorial → Washington Monument → Capitol) |

`--track` works with `--topic location` (recommended) or `--topic tag-reads`
(attaches the moving GPS to every tag-read payload).

> **Where do the rows land?** The two topics feed different tables and show up
> in different UI views — pick by where you want the data to appear:
>
> | Topic | Table | Row shape | Where to view |
> | ----- | ----- | --------- | ------------- |
> | `tag-reads` (default) | `tag_reads` | One row per publish, lat/lon as columns on the read | **Tag Reads** grid (lat/lon columns populated) |
> | `location` | `telemetry_readings` | Two rows per publish (`metric_name='location.latitude'`, `'location.longitude'`) | Map / asset-trail views; **not** the Tag Reads grid |
>
> If you ran `drive_track.sh` (which uses `--topic location`) and expected to
> see waypoints in the Tag Reads grid, switch to tag-reads mode instead:
>
> ```bash
> python3 examples/paho_smoke_publisher.py --topic tag-reads \
>   --track examples/tracks/boston-loop.csv --track-interp 0.5
> ```

---

## Tests

```bash
pip install -e '.[test]'
pytest
```

Pure-logic modules (`dedup`, `buffer`, `clock`) are fully unit-tested. The
MQTT transport has a fake-broker test that exercises reconnect/backoff.
