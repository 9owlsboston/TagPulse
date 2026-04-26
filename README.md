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

- **MQTT ingestion** — devices publish tag reads and sensor data via MQTT
- **FastAPI backend** — REST API for device management, data queries, and analytics
- **TimescaleDB** — time-series storage for tag reads + relational storage for device registry
- **Plugin analytics** — analytics modules as internal Python packages

See [docs/architecture.md](docs/architecture.md) for the full system overview.

## Project Structure

```
src/
  tagpulse/
    api/          # FastAPI routes
    ingestion/    # MQTT subscriber + message processing
    models/       # Database models (SQLAlchemy + TimescaleDB)
    analytics/    # Pluggable analytics modules
    core/         # Config, dependencies, shared utilities
tests/
  unit/           # Fast, isolated tests
  integration/    # Cross-component tests
docs/             # Architecture, ADRs, runbooks
```
