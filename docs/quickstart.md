# TagPulse Quick Start Guide

Get the full TagPulse platform running locally in under 5 minutes.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ (backend development)
- Node.js 20+ (frontend development)
- Git
- Two terminal windows/tabs

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
2. Send a tag read from each device every 2 seconds
3. Pick from 50 random tag IDs with realistic signal strength (-80 to -20 dBm)
4. Include sensor data (temperature readings)
5. Run until you press Ctrl+C

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
docker compose up
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

## Tear Down

```bash
cd TagPulse
docker compose down       # Stop containers
docker compose down -v    # Stop + wipe all data (fresh start)
```
