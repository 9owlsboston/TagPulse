# TagPulse

IoT platform for RFID tag readers and sensor data. Ingests device telemetry, manages device registry, and runs pluggable analytics modules tailored to application needs.

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Run quality gates
make check

# Start the development server
make run
```

## Architecture

- **Device registry & config** — register, configure, and monitor IoT device fleet
- **Dual ingestion** — MQTT and HTTP endpoints for device telemetry
- **TimescaleDB** — time-series storage for tag reads + relational storage for device registry
- **Rules & alerts** — user-defined rules evaluated against telemetry, with webhook/email alert routing
- **Plugin analytics** — analytics modules as internal Python packages
- **Integration layer** — outbound webhooks, SSE streaming, scheduled data exports
- **Admin UI** — device management, telemetry dashboards, rule/alert configuration (Q3)

See [docs/architecture.md](docs/architecture.md) for the full system overview.

## Project Structure

```
src/
  tagpulse/
    api/          # FastAPI routes
    ingestion/    # MQTT + HTTP ingestion endpoints
    models/       # Database models (SQLAlchemy + TimescaleDB)
    rules/        # Rules engine + alert routing
    analytics/    # Pluggable analytics modules
    integrations/ # Webhooks, SSE, scheduled exports
    core/         # Config, dependencies, shared utilities
tests/
  unit/           # Fast, isolated tests
  integration/    # Cross-component tests
docs/             # Architecture, ADRs, runbooks
```
