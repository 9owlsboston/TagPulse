# ADR-009: Containerization and Local Development Environment

**Status:** proposed
**Date:** 2026-04-25

## Context

TagPulse depends on three runtime components: the FastAPI application, a TimescaleDB database, and an MQTT broker. Today, developers must install and configure each dependency manually. There is no Dockerfile, no Compose file, and no documented way to stand up the full stack locally.

We need:

1. A **containerized application** image for consistent deployment across environments.
2. A **local development environment** that any contributor can start with a single command.
3. A path to **production deployment** (Kubernetes, cloud container services) without re-architecting.

## Decision

### Application Container

Build a multi-stage Docker image:

```dockerfile
# -- build stage --
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# -- runtime stage --
FROM python:3.12-slim
WORKDIR /app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY src/ src/
EXPOSE 8000
CMD ["uvicorn", "tagpulse.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Key choices:
- **`python:3.12-slim`** base — small image, no unnecessary OS packages.
- **Multi-stage build** — build dependencies don't ship in the runtime image.
- **No `.env` baked in** — all config via environment variables (12-factor).
- **Non-root user** added in production variant.

### Docker Compose for Local Development

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://tagpulse:secret@db:5432/tagpulse
      MQTT_BROKER_HOST: mqtt
      MQTT_BROKER_PORT: 1883
      LOG_LEVEL: debug
    depends_on:
      db:
        condition: service_healthy
      mqtt:
        condition: service_started
    volumes:
      - ./src:/app/src  # hot-reload in dev
    command: >
      uvicorn tagpulse.api.main:app
      --host 0.0.0.0 --port 8000 --reload
      --reload-dir /app/src

  db:
    image: timescale/timescaledb:latest-pg16
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: tagpulse
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: tagpulse
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tagpulse"]
      interval: 5s
      timeout: 3s
      retries: 5

  mqtt:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./docker/mosquitto.conf:/mosquitto/config/mosquitto.conf

volumes:
  pgdata:
```

### Developer Workflows

| Task | Command |
|------|---------|
| Start full stack | `docker compose up` |
| Start infra only, app on host | `docker compose up -d db mqtt` then `make run` |
| Run tests against Compose infra | `docker compose up -d db mqtt` then `make test` |
| Rebuild after dependency change | `docker compose build app` |
| Tear down and wipe data | `docker compose down -v` |

### Makefile Additions

```makefile
dc-up:       ## Start all services (foreground)
	docker compose up

dc-infra:    ## Start infra only (background)
	docker compose up -d db mqtt

dc-down:     ## Stop all services
	docker compose down

dc-reset:    ## Stop and wipe volumes
	docker compose down -v
```

### MQTT Broker Configuration

Minimal `docker/mosquitto.conf`:

```
listener 1883
allow_anonymous true
```

Anonymous access is acceptable for local dev. Production deployments use ACLs and TLS (see ADR-008 for tenant-scoped ACLs).

### Integration Test Support

A `docker-compose.test.yml` override can provide ephemeral, isolated services for CI:

```yaml
services:
  db:
    tmpfs: /var/lib/postgresql/data  # fast, disposable
  mqtt:
    # no port exposure needed — tests connect via Docker network
    ports: []
```

### Production Path

- The same Dockerfile (without `--reload` and source mount) runs in production.
- Orchestration via Kubernetes, AWS ECS, or Azure Container Apps — all consume the same image.
- Environment-specific config injected via env vars or secrets managers.
- Health check endpoint (`GET /health`) already exists for container orchestrator probes.

## Consequences

- **Good:** Single `docker compose up` to start the full platform — zero manual dependency setup.
- **Good:** Identical container image across dev, CI, staging, production.
- **Good:** Source mount + `--reload` preserves fast local iteration.
- **Good:** TimescaleDB and Mosquitto are official images with long-term support.
- **Bad:** Docker Desktop license required for commercial use on macOS/Windows (free alternatives: Podman, Colima).
- **Bad:** Source mount doesn't work in rootless Docker without UID mapping. Documented workaround needed.
- **Bad:** Local Compose is not a production environment — differences in networking, TLS, and secrets management must be documented.

## Alternatives Considered

- **Devcontainers (VS Code):** Good DX but couples the project to VS Code. Can be added later as an optional layer on top of Compose.
- **Nix / devenv:** Reproducible but steep learning curve and poor Windows support. Rejected for now.
- **Vagrant:** Heavy, slow. Containers are a better fit for this stack.
