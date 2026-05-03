# TagPulse Quick Start Guide

Get the full TagPulse platform running locally in under 5 minutes.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ (backend development)
- Node.js 20+ (frontend development)
- Git
- Two terminal windows/tabs

### Corporate proxy (WSL)

If you're behind a corporate proxy (e.g. `wsl-gsa-proxy`), it may intercept requests to `localhost`. Add this to your `~/.bashrc` (or run before each session):

```bash
export NO_PROXY=localhost,127.0.0.1
```

Alternatively, replace `localhost` with `127.0.0.1` in any `curl` or browser URL throughout this guide. The device simulator (`scripts/simulate_devices.py`) also requires this variable:

```bash
NO_PROXY=localhost,127.0.0.1 python scripts/simulate_devices.py --tenant-id <UUID> --devices 5 --interval 2
```

---

## 1. Clone Both Repos

```bash
git clone https://github.com/9owlsboston/TagPulse.git
git clone https://github.com/9owlsboston/TagPulse-UI.git
```

---

## 2. Start Infrastructure

```bash
cd TagPulse
docker compose up -d db mqtt
```

Wait for the DB health check (~5 seconds):

```bash
docker compose exec db pg_isready -U tagpulse
```

---

## 3. Install Backend + Run Migrations

```bash
cd TagPulse
pip install -e ".[dev]"
alembic upgrade head
```

---

## 4. Two-Terminal Development Workflow

### Terminal 1 — Backend API (port 8000)

```bash
cd ~/TagPulse
make run
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Terminal 2 — Frontend (port 5173)

```bash
cd ~/TagPulse-UI
npm install        # first time only
npm run dev
```

Open http://localhost:5173 in your browser.

Both servers hot-reload — edit Python or React and changes appear instantly.

API docs: http://localhost:8000/docs

---

## 5. Seed a Tenant

```bash
docker compose exec db psql -U tagpulse -d tagpulse -c "
INSERT INTO tenants (id, name, slug, plan, status)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Test Corp',
  'test-corp',
  'standard',
  'active'
);
"
```

---

## 5b. Bootstrap an Admin API Key

A fresh tenant has no users, so the UI cannot log in via the **API Key** tab and the `Authorization: Bearer …` header doesn't work for any privileged API call. Bootstrap the first admin once:

```bash
# 1. Insert an admin user (idempotent — re-running just upserts the role)
docker compose exec -T db psql -U tagpulse -d tagpulse -c "
INSERT INTO users (id, tenant_id, email, name, role, status)
VALUES (gen_random_uuid(), '11111111-1111-1111-1111-111111111111',
        'admin@example.com', 'Admin', 'admin', 'active')
ON CONFLICT (tenant_id, email) DO UPDATE SET role = 'admin', status = 'active';
"

# 2. Generate a key (prints KEY=, PREFIX=, HASH= — copy them)
python -c "
from tagpulse.core.user_auth import generate_api_key
raw, prefix, h = generate_api_key('test-corp')
print(f'KEY={raw}')
print(f'PREFIX={prefix}')
print(f'HASH={h}')
"

# 3. Attach the hash + prefix to the user (paste the values from step 2)
docker compose exec -T db psql -U tagpulse -d tagpulse -c "
UPDATE users
SET api_key_hash='<HASH from step 2>',
    api_key_prefix='<PREFIX from step 2>'
WHERE tenant_id='11111111-1111-1111-1111-111111111111'
  AND email='admin@example.com';
"
```

> **Store the `KEY` value securely** — only the SHA-256 hash is kept in the DB; the plaintext key is never recoverable. Lost keys must be regenerated via the UI (**Admin → Users → Regenerate API Key**) or by repeating step 2/3.

**Log into the UI:**

1. Open http://localhost:5173 (use `127.0.0.1` if behind a localhost-intercepting proxy).
2. Pick the **API Key** tab.
3. Email: `admin@example.com`
4. API Key: paste the `KEY` value from step 2 (looks like `tp_test-corp_<hex>`).
5. Click **Sign In** — you'll land on the dashboard with the **admin** badge in the header and the full sidebar (Tenant Settings, Users, Audit Log).

**Use the same key for API calls:**

```bash
curl -H "Authorization: Bearer tp_test-corp_<your-hex>" \
     http://localhost:8000/admin/audit-logs
```

---

---

## 6. Device Simulator

TagPulse includes a device simulator that creates fake RFID readers and sends continuous tag reads.

### Seed devices + run continuous simulation

```bash
cd ~/TagPulse
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 5 \
  --interval 2
```

This will:
1. Create 5 simulated RFID readers (Sim-Reader-01 through 05)
2. Send a tag read from each device every ~2 seconds (±30% jitter)
3. Pick from 50 random tag IDs with realistic signal strength (-80 to -20 dBm)
4. Include sensor data (temperature always; humidity 30%, battery 10% of reads)
5. Simulate real-world noise: 10% chance a device skips a cycle, 5% chance a read drops
6. Run until you press Ctrl+C

### Seed devices only (no continuous reads)

```bash
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 10 \
  --seed-only
```

### Run for a fixed duration

```bash
python scripts/simulate_devices.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  --devices 3 \
  --interval 1 \
  --duration 60    # Run for 60 seconds then stop
```

### Simulator options

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `--devices` | 3 | Number of simulated RFID readers to create |
| `--interval` | 2.0 | Seconds between reads per device |
| `--duration` | 0 | Run for N seconds (0 = forever, Ctrl+C to stop) |
| `--seed-only` | false | Create devices and exit (no tag reads) |

---

## 7. Manual Testing

### Register a device manually

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{"name": "Manual-Reader-01", "device_type": "rfid_reader"}' \
  http://localhost:8000/device-registry
```

### Ingest a tag read (HTTP)

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG001",
    "timestamp": "2026-04-26T12:00:00Z",
    "signal_strength": -45.0
  }' \
  http://localhost:8000/tag-reads
```

### Ingest a tag read (MQTT)

```bash
docker compose exec mqtt mosquitto_pub \
  -t "tenants/11111111-1111-1111-1111-111111111111/devices/<DEVICE_ID>/tag-reads" \
  -m '{"tag_id": "TAG002", "timestamp": "2026-04-26T12:01:00Z", "signal_strength": -50.0}'
```

---

## 8. Verify the Full Flow

```bash
# List tag reads
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/tag-reads

# Device health
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/device-health

# Analytics (read frequency)
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/analytics/read-frequency

# Prometheus metrics
curl http://localhost:8000/metrics

# Readiness check
curl http://localhost:8000/health/ready
```

---

## 9. Create a Rule + Test Alerting

```bash
# Create a threshold rule
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Weak signal alert",
    "condition_type": "threshold",
    "condition_config": {"field": "signal_strength", "operator": "lt", "value": -60},
    "action_type": "notification",
    "action_config": {}
  }' \
  http://localhost:8000/rules

# Ingest a tag read with weak signal (triggers the rule)
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG003",
    "timestamp": "2026-04-26T12:02:00Z",
    "signal_strength": -75.0
  }' \
  http://localhost:8000/tag-reads

# Check alerts
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/alerts
```

---

## Alternative: Full Stack with Docker Compose

Run everything in containers (no local Python/Node needed):

```bash
cd TagPulse
docker compose up -d
```

Run migrations (required on first start):

```bash
pip install -e ".[dev]"    # needed for alembic CLI
DATABASE_URL=postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse alembic upgrade head
```

Seed a test tenant:

```bash
docker compose exec db psql -U tagpulse -d tagpulse -c "
INSERT INTO tenants (id, name, slug, plan, status)
VALUES (
  '11111111-1111-1111-1111-111111111111',
  'Test Corp',
  'test-corp',
  'standard',
  'active'
);
"
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

---

## Service Reference

| Service | Port | Purpose |
|---------|------|---------|
| Frontend (dev) | 5173 | Vite dev server with hot-reload |
| Frontend (prod) | 3000 | nginx serving built React SPA |
| Backend API | 8000 | FastAPI (all REST endpoints) |
| API Docs | 8000/docs | Swagger UI (auto-generated) |
| Health | 8000/health/ready | Readiness probe (DB + MQTT + EventBus) |
| Health Detail | 8000/health/detail | Queue sizes, meter status |
| Metrics | 8000/metrics | Prometheus / OpenTelemetry |
| TimescaleDB | 5432 | `psql -U tagpulse -d tagpulse` |
| MQTT Broker | 1883 | Mosquitto (`mosquitto_pub` / `mosquitto_sub`) |

---

## Quality Gates

### Backend

```bash
cd TagPulse
make check    # lint + typecheck + test (140 tests)
```

### Frontend

```bash
cd TagPulse-UI
npm run check    # lint + typecheck + test
```

---

## Load Testing

TagPulse includes a load test harness for stress testing the ingestion pipeline.

### Burst test — max throughput

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 10 --total 1000
```

Sends 1,000 reads as fast as possible with 10 concurrent workers.

### Sustained rate test

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 20 --rps 200 --duration 60
```

Holds 200 req/s for 60 seconds (12,000 total reads).

### Ramp-up test

```bash
python scripts/load_test.py \
  --tenant-id 11111111-1111-1111-1111-111111111111 \
  -w 30 --rps 50 --ramp 50 --ramp-step 10 --rps-max 500 --duration 120
```

Starts at 50 rps, adds 50 every 10 seconds, caps at 500 rps.

### Load test options

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-id` | (required) | Tenant UUID |
| `-w`, `--workers` | 10 | Concurrent async workers |
| `--devices` | 20 | Number of load-test devices to create |
| `--total` | 0 | Total requests (0 = use `--duration`) |
| `--duration` | 30 | Test length in seconds |
| `--rps` | 0 | Target requests/sec (0 = max throughput) |
| `--ramp` | 0 | Increase rps by this amount each step |
| `--ramp-step` | 10 | Seconds between ramp increases |
| `--rps-max` | 10x rps | Cap when ramping |

Results include throughput, p50/p95/p99 latencies, status codes, and error breakdown.

---

## Remote IoT Testing with ngrok

Expose your local TagPulse instance to the internet so real IoT devices (or remote simulators) can send data without deploying to a cloud host.

### Prerequisites

- [ngrok](https://ngrok.com/) installed and authenticated (`ngrok config add-authtoken <TOKEN>`)
- TagPulse backend running locally on port 8000
- MQTT broker running locally on port 1883

### Tunnel the HTTP API (for devices using REST ingestion)

```bash
ngrok http 8000
```

ngrok prints a public URL like `https://a1b2c3d4.ngrok-free.app`. Devices can now push tag reads over the internet:

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "<DEVICE_ID>",
    "tag_id": "TAG-REMOTE-001",
    "timestamp": "2026-04-26T14:00:00Z",
    "signal_strength": -42.0
  }' \
  https://a1b2c3d4.ngrok-free.app/tag-reads
```

API docs are also available at `https://a1b2c3d4.ngrok-free.app/docs`.

### Tunnel MQTT (for devices using MQTT ingestion)

```bash
ngrok tcp 1883
```

ngrok prints a TCP address like `tcp://0.tcp.ngrok.io:12345`. Point your RFID reader's MQTT client at it:

| Setting | Value |
|---------|-------|
| Broker host | `0.tcp.ngrok.io` |
| Broker port | `12345` (from ngrok output) |
| Topic | `tenants/{tenant_id}/devices/{device_id}/tag-reads` |
| QoS | 1 |

Test from any remote machine:

```bash
mosquitto_pub \
  -h 0.tcp.ngrok.io -p 12345 \
  -t "tenants/11111111-1111-1111-1111-111111111111/devices/<DEVICE_ID>/tag-reads" \
  -m '{"tag_id": "TAG-REMOTE-002", "timestamp": "2026-04-26T14:01:00Z", "signal_strength": -55.0}'
```

### Tunnel both simultaneously

Open two terminal tabs:

```bash
# Terminal A — HTTP API
ngrok http 8000

# Terminal B — MQTT broker
ngrok tcp 1883
```

Or use an ngrok config file (`~/.config/ngrok/ngrok.yml`):

```yaml
version: 3
tunnels:
  tagpulse-api:
    proto: http
    addr: 8000
  tagpulse-mqtt:
    proto: tcp
    addr: 1883
```

Then start both tunnels at once:

```bash
ngrok start tagpulse-api tagpulse-mqtt
```

### Security considerations

- **ngrok free tier** URLs are public — anyone with the URL can hit your API. Use the `X-Tenant-ID` header auth (or Bearer tokens from Sprint 12) to restrict access.
- **Do not** leave tunnels running unattended in production.
- For persistent testing, consider ngrok's reserved domains (`ngrok http --url=tagpulse.ngrok-free.app 8000`) so the URL stays stable across restarts.
- MQTT tunnels are unauthenticated by default. Add Mosquitto `password_file` or `allow_anonymous false` in `docker/mosquitto.conf` before exposing over the internet.

### Verify remote ingestion

After sending data through ngrok, confirm it arrived:

```bash
curl -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  http://localhost:8000/tag-reads?tag_id=TAG-REMOTE-001
```

---

## Tear Down

```bash
cd TagPulse
docker compose down       # Stop containers
docker compose down -v    # Stop + wipe all data (fresh start)
```
