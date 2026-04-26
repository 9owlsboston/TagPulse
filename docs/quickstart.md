# TagPulse Quick Start Guide

Get the full TagPulse platform running locally in under 5 minutes.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.12+ (backend development)
- Node.js 20+ (frontend development)
- Git

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

## 4. Start the Backend API

```bash
make run
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/health/ready
# {"status":"healthy","checks":{"database":{"status":"up",...},...}}
```

API docs available at: http://localhost:8000/docs

---

## 5. Start the Frontend

```bash
cd TagPulse-UI
npm install
npm run dev
```

Open http://localhost:5173 in your browser.

---

## 6. Seed Test Data

### Create a tenant

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

### Register a device

```bash
curl -X POST \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Content-Type: application/json" \
  -d '{"name": "Reader-01", "device_type": "rfid_reader"}' \
  http://localhost:8000/device-registry
```

Save the `id` from the response — you'll need it below.

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

## 7. Verify the Full Flow

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
```

---

## 8. Create a Rule + Test Alerting

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
