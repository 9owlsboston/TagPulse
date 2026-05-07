"""Sprint 22 Phase A: cloud-readiness config + middleware tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.core.config import Settings
from tagpulse.core.migration_check import expected_head_revision
from tagpulse.core.rate_limit import (
    RATE_LIMITER,
    RateLimiter,
    classify_route,
    rate_limit_middleware,
)

# -----------------------------------------------------------------------------
# A1 — strict-mode Settings validator
# -----------------------------------------------------------------------------


class TestSettingsStrictMode:
    def test_dev_default_keeps_dev_secrets(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.environment == "dev"
        assert s.jwt_secret == "dev-secret-change-in-production"  # noqa: S105
        # Strict migration check is opt-in in dev.
        assert s.strict_migration_check is False

    def test_production_with_dev_jwt_raises(self) -> None:
        with pytest.raises(ValueError, match="jwt_secret"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                environment="production",
                database_url="postgresql+asyncpg://u:p@db:5432/d",
                cors_origins="https://app.example.com",
            )

    def test_production_with_dev_database_url_raises(self) -> None:
        with pytest.raises(ValueError, match="database_url"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                environment="production",
                jwt_secret="x" * 32,
                cors_origins="https://app.example.com",
            )

    def test_production_rejects_wildcard_cors(self) -> None:
        with pytest.raises(ValueError, match="cors_origins"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                environment="production",
                jwt_secret="x" * 32,
                database_url="postgresql+asyncpg://u:p@db:5432/d",
                cors_origins="*",
            )

    def test_production_rejects_blank_cors_entry(self) -> None:
        with pytest.raises(ValueError, match="blank entry"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                environment="production",
                jwt_secret="x" * 32,
                database_url="postgresql+asyncpg://u:p@db:5432/d",
                cors_origins="https://a.example.com,,https://b.example.com",
            )

    def test_production_forces_strict_migration_check(self) -> None:
        s = Settings(  # type: ignore[call-arg]
            _env_file=None,
            environment="production",
            jwt_secret="x" * 32,
            database_url="postgresql+asyncpg://u:p@db:5432/d",
            cors_origins="https://app.example.com",
        )
        assert s.strict_migration_check is True

    def test_staging_subject_to_same_rules(self) -> None:
        with pytest.raises(ValueError, match="jwt_secret"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                environment="staging",
                database_url="postgresql+asyncpg://u:p@db:5432/d",
                cors_origins="https://staging.example.com",
            )


# -----------------------------------------------------------------------------
# A4 — rate limiter
# -----------------------------------------------------------------------------


class TestClassifyRoute:
    def test_admin_path_classified_as_admin(self) -> None:
        assert classify_route("GET", "/admin/usage") == "admin"
        assert classify_route("POST", "/admin/audit-logs") == "admin"

    def test_post_tag_reads_is_ingest(self) -> None:
        assert classify_route("POST", "/tag-reads") == "ingest"
        assert classify_route("POST", "/telemetry") == "ingest"
        assert classify_route("POST", "/telemetry/readings/ingest") == "ingest"

    def test_get_is_read(self) -> None:
        assert classify_route("GET", "/devices") == "read"
        assert classify_route("HEAD", "/devices") == "read"

    def test_other_writes_are_write(self) -> None:
        assert classify_route("PATCH", "/devices/123") == "write"
        assert classify_route("DELETE", "/devices/123") == "write"
        assert classify_route("POST", "/rules") == "write"


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_consume_until_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tagpulse.core import rate_limit as rl_mod

        monkeypatch.setattr(rl_mod.settings, "rate_limit_read_per_min", 3)
        limiter = RateLimiter()
        # No DB lookup — patch the override fetch to avoid touching SQL.
        async def _no_overrides(_: uuid.UUID) -> dict[str, int]:
            return {}

        monkeypatch.setattr(limiter, "_fetch_override", _no_overrides)
        tid = uuid.uuid4()
        for _ in range(3):
            allowed, _ = await limiter.check(tid, "read")
            assert allowed is True
        allowed, limit = await limiter.check(tid, "read")
        assert allowed is False
        assert limit == 3

    @pytest.mark.asyncio
    async def test_per_tenant_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tagpulse.core import rate_limit as rl_mod

        monkeypatch.setattr(rl_mod.settings, "rate_limit_read_per_min", 1)
        limiter = RateLimiter()

        async def _no_overrides(_: uuid.UUID) -> dict[str, int]:
            return {}

        monkeypatch.setattr(limiter, "_fetch_override", _no_overrides)
        a, b = uuid.uuid4(), uuid.uuid4()
        assert (await limiter.check(a, "read"))[0] is True
        assert (await limiter.check(a, "read"))[0] is False
        # b has its own bucket.
        assert (await limiter.check(b, "read"))[0] is True

    @pytest.mark.asyncio
    async def test_override_supersedes_global(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tagpulse.core import rate_limit as rl_mod

        monkeypatch.setattr(rl_mod.settings, "rate_limit_read_per_min", 1)
        limiter = RateLimiter()
        tid = uuid.uuid4()

        async def _override(_: uuid.UUID) -> dict[str, int]:
            return {"read": 5}

        monkeypatch.setattr(limiter, "_fetch_override", _override)
        for _ in range(5):
            assert (await limiter.check(tid, "read"))[0] is True
        assert (await limiter.check(tid, "read"))[0] is False


class TestRateLimitMiddleware:
    def _build(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        from tagpulse.core import rate_limit as rl_mod

        monkeypatch.setattr(rl_mod.settings, "rate_limit_read_per_min", 2)
        RATE_LIMITER.reset()

        async def _no_overrides(_: uuid.UUID) -> dict[str, int]:
            return {}

        monkeypatch.setattr(RATE_LIMITER, "_fetch_override", _no_overrides)

        app = FastAPI()
        app.middleware("http")(rate_limit_middleware)

        @app.get("/devices")
        async def _devices() -> dict[str, str]:
            return {"ok": "yes"}

        @app.get("/health")
        async def _health() -> dict[str, str]:
            return {"status": "ok"}

        return TestClient(app)

    def test_health_path_bypasses_limiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._build(monkeypatch)
        for _ in range(10):
            assert client.get("/health").status_code == 200

    def test_no_tenant_header_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._build(monkeypatch)
        # Without X-Tenant-ID we have no quota anchor — limiter abstains.
        for _ in range(5):
            assert client.get("/devices").status_code == 200

    def test_per_tenant_429_after_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._build(monkeypatch)
        tid = str(uuid.uuid4())
        headers = {"X-Tenant-ID": tid}
        assert client.get("/devices", headers=headers).status_code == 200
        assert client.get("/devices", headers=headers).status_code == 200
        r = client.get("/devices", headers=headers)
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "60"
        body = r.json()
        assert body["route_class"] == "read"
        assert body["limit_per_min"] == 2


# -----------------------------------------------------------------------------
# A5/A6 — health endpoint shape
# -----------------------------------------------------------------------------


class TestHealthEndpoints:
    def test_live_alias_exists(self) -> None:
        from tagpulse.api.routes.health import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        assert client.get("/health/live").status_code == 200
        assert client.get("/health").status_code == 200


# -----------------------------------------------------------------------------
# A7 — migration head discovery
# -----------------------------------------------------------------------------


class TestMigrationCheck:
    def test_expected_head_revision_returns_string(self) -> None:
        head = expected_head_revision()
        assert isinstance(head, str)
        assert head  # non-empty
        # Sprint 22 just shipped 033; head should be at least 033.
        assert head >= "033"
