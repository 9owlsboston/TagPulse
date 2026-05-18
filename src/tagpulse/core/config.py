"""Application configuration via environment variables.

Sprint 22 (cloud readiness, ADR-016) introduces a strict-mode contract:
when ``environment`` is ``staging`` or ``production`` the process refuses
to start with the dev defaults still in place. The dev workflow
(``make run``, ``scripts/smoke_setup.py``, the unit-test suite) is
unaffected because ``environment`` defaults to ``dev``.
"""

import re
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

# Sentinel values used by the strict-mode validator. Any deployment that
# leaves these in place in staging/production is misconfigured.
_DEV_JWT_SECRET = "dev-secret-change-in-production"  # noqa: S105 — sentinel, not a credential
_DEV_DATABASE_URL = "postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse"


class Settings(BaseSettings):
    """TagPulse application settings."""

    # -- Sprint 22 A1: deployment environment. Drives strict-mode startup
    # checks for secrets, CORS, and migration version. --
    environment: Literal["dev", "staging", "production"] = "dev"

    database_url: str = _DEV_DATABASE_URL
    database_config_path: str = "config/database.yaml"
    database_backend: Literal["timescale", "postgres"] = "timescale"
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    # Sprint 28 C6 — server-TLS to Mosquitto on port 8883. Default off so
    # this commit deploys without flipping any clients. Cutover order is
    # documented in docs/runbooks/mqtt-outage.md §"TLS cutover".
    # ``mqtt_tls_ca_path`` points to a PEM file inside the worker container
    # (mounted as a secret-volume or written from KV at boot); empty means
    # use the system CA bundle.
    mqtt_use_tls: bool = False
    mqtt_tls_ca_path: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"
    event_bus_capacity: int = 10_000

    # -- Sprint 22 A2: CORS. ``cors_origins`` is comma-separated. In
    # non-dev environments the strict-mode validator rejects ``*`` and
    # any blank entry. ``allow_methods`` / ``allow_headers`` default to
    # the explicit list TagPulse actually uses (was ``["*"]``). --
    cors_origins: str = "http://localhost:5173"
    cors_allow_methods: str = "GET,POST,PATCH,PUT,DELETE,OPTIONS"
    cors_allow_headers: str = "Authorization,Content-Type,X-Tenant-ID,X-Request-ID,X-API-Key"
    # cors_origin_regex is forwarded to Starlette CORSMiddleware as
    # ``allow_origin_regex``. Required to allow Azure Static Web App preview
    # slot URLs (e.g. ``<basename>-42.centralus.7.azurestaticapps.net``) which
    # cannot be enumerated ahead of time in ``cors_origins``. Empty string
    # means "no regex" — only the explicit ``cors_origins`` allow-list
    # applies. CORSMiddleware ORs the two, so a request matching EITHER
    # passes the preflight.
    cors_origin_regex: str = ""
    # Sprint 25 A2: CORS preflight max-age. The SPA fires an OPTIONS preflight
    # before the first call from any new tab; caching it for 600s shaves
    # 60-80ms off the first-paint-to-login-button time on cold tabs. Default 0
    # in dev so the Vite proxy doesn't fight us; validator forces 600 in non-
    # dev when left at 0 (explicit non-zero values survive untouched).
    cors_preflight_max_age_seconds: int = 0

    # Sprint 25 A1: build identity surfaced on /health/live for SPA polling.
    # ``build_version`` should be the short git SHA; ``build_time`` an ISO-8601
    # UTC timestamp. The Dockerfile populates both via build args; dev keeps
    # the sentinels.
    build_version: str = "dev"
    build_time: str = "unknown"

    jwt_secret: str = _DEV_JWT_SECRET
    jwt_expiry_seconds: int = 3600
    login_rate_limit: int = 5

    # Sprint 16 — edge contract enforcement
    # Per docs/design/edge-device-contract.md §10 — start in observe mode for
    # 48h, then flip to enforce. Observe-mode logs + meters but does not reject.
    ingest_clock_enforce: bool = True
    # Per §3.4 / §4 — explicit ingest payload size cap (256 KB).
    max_ingest_payload_bytes: int = 262_144

    # Sprint 17a — geofencing & map.
    # Off by default per docs/design/geofencing-and-map.md §10 rollout step 2:
    # ship the migration + UI hidden, then flip the flag in production once the
    # bbox index is live and the map UI is ready.
    geofence_evaluation_enabled: bool = False
    # Per §5.2 dwell-worker scan interval (seconds).
    dwell_worker_interval_s: int = 60

    # -- Sprint 22 A4: global rate limiter. Per-(tenant, route_class)
    # token bucket. Limits are requests-per-minute; per-tenant overrides
    # are stored in ``tenants.rate_limit_overrides`` (migration 033). --
    rate_limit_enabled: bool = True
    rate_limit_ingest_per_min: int = 6_000  # 100 events/sec default ceiling
    rate_limit_read_per_min: int = 600
    rate_limit_write_per_min: int = 300
    rate_limit_admin_per_min: int = 120

    # -- Sprint 22 A7: startup migration-version assertion. Default
    # ``True`` in staging/production (forced on by validator), ``False``
    # in dev so ``make run`` against an in-flight migration branch keeps
    # working. Set to ``False`` explicitly to override. --
    strict_migration_check: bool = False

    # -- Sprint 22 B1: worker process split. When ``True`` (default,
    # dev/test/single-container compatibility) the FastAPI ``lifespan``
    # also boots the inventory + dwell + alert-delivery + analytics +
    # webhook + MQTT-subscriber components. Production cloud deployments
    # set this to ``False`` on the API container and run a separate
    # worker container (same image, ``WORKERS_INLINE=true``) so HTTP and
    # background workers scale independently. ``event_bus`` and
    # ``usage_meter`` are always created — HTTP routes depend on them. --
    workers_inline: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("cors_origin_regex")
    @classmethod
    def _validate_cors_origin_regex(cls, v: str) -> str:
        """Compile-check the regex at boot so a typo fails fast.

        Starlette CORSMiddleware lazily compiles ``allow_origin_regex`` on
        the first preflight, which would surface as a 500 mid-request
        instead of a clear startup error. Validate here in all environments.
        """
        if v:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"cors_origin_regex is not a valid regex: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _enforce_strict_mode(self) -> "Settings":
        """ADR-016 §7: refuse to boot in non-dev with dev sentinels."""
        if self.environment == "dev":
            return self

        problems: list[str] = []
        if self.jwt_secret == _DEV_JWT_SECRET or not self.jwt_secret:
            problems.append(
                "jwt_secret must be set to a non-default value when "
                "environment != 'dev' (set JWT_SECRET env var)"
            )
        if self.database_url == _DEV_DATABASE_URL:
            problems.append(
                "database_url must be set to a non-default value when "
                "environment != 'dev' (set DATABASE_URL env var)"
            )
        origins = [o.strip() for o in self.cors_origins.split(",")]
        if "*" in origins:
            problems.append(
                "cors_origins must not contain '*' when "
                "environment != 'dev' (set CORS_ORIGINS to an explicit "
                "comma-separated allow-list)"
            )
        if any(not o for o in origins):
            problems.append("cors_origins contains a blank entry; check for stray commas")
        # Sprint 25 A2: in non-dev, default the preflight max-age to 600s if it
        # was left at 0. Explicit non-zero values (set via env) survive.
        if self.cors_preflight_max_age_seconds == 0:
            self.cors_preflight_max_age_seconds = 600
        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(
                f"Refusing to start in environment='{self.environment}':\n  - {joined}"
            )
        # Force strict migration check in non-dev.
        if not self.strict_migration_check:
            self.strict_migration_check = True
        return self


settings = Settings()
