"""Sprint 25 Phase A — backend health & CSP support."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes import security as security_module
from tagpulse.api.routes.health import router as health_router
from tagpulse.api.routes.security import router as security_router
from tagpulse.core.config import Settings


@pytest.fixture()
def health_client() -> TestClient:
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


@pytest.fixture()
def security_client() -> TestClient:
    """Fresh app + reset per-IP bucket so tests don't bleed into each other."""
    security_module._recent_reports.clear()
    app = FastAPI()
    app.include_router(security_router)
    return TestClient(app)


# -----------------------------------------------------------------------------
# A1 — /health/live contract
# -----------------------------------------------------------------------------


class TestHealthLiveContract:
    def test_returns_alive_shape(self, health_client: TestClient) -> None:
        response = health_client.get("/health/live")
        assert response.status_code == 200
        body = response.json()
        # Required keys (Sprint 25 A1 contract).
        assert set(body.keys()) >= {"status", "version", "build_time"}
        assert body["status"] == "alive"
        assert isinstance(body["version"], str) and body["version"]
        assert isinstance(body["build_time"], str) and body["build_time"]

    def test_no_store_cache_header(self, health_client: TestClient) -> None:
        response = health_client.get("/health/live")
        assert response.headers.get("cache-control") == "no-store"

    def test_responds_under_50ms_no_dependencies(self, health_client: TestClient) -> None:
        # Loose budget: TestClient overhead can be lumpy on slow CI runners.
        # The point is to assert this path does no DB / MQTT / migration work.
        # If a future contributor adds a dep here, the budget will break.
        start = time.monotonic()
        response = health_client.get("/health/live")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 200, f"/health/live took {elapsed_ms:.1f}ms"

    def test_legacy_health_unchanged(self, health_client: TestClient) -> None:
        # /health is the back-compat surface for k8s probes that pre-date /live.
        response = health_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# -----------------------------------------------------------------------------
# A2 — CORS preflight max-age
# -----------------------------------------------------------------------------


class TestCorsPreflightMaxAge:
    def test_dev_default_is_zero(self) -> None:
        s = Settings(environment="dev")
        assert s.cors_preflight_max_age_seconds == 0

    def test_non_dev_forces_default_600(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db?ssl=require")
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
        s = Settings(environment="staging")
        assert s.cors_preflight_max_age_seconds == 600

    def test_non_dev_explicit_override_survives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db?ssl=require")
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
        monkeypatch.setenv("CORS_PREFLIGHT_MAX_AGE_SECONDS", "120")
        s = Settings(environment="staging")
        assert s.cors_preflight_max_age_seconds == 120


# -----------------------------------------------------------------------------
# A3 — /security/csp-report
# -----------------------------------------------------------------------------


_LEGACY_REPORT = {
    "csp-report": {
        "document-uri": "https://app.example.com/devices",
        "violated-directive": "script-src 'self'",
        "blocked-uri": "https://evil.example.com/x.js",
        "source-file": "https://app.example.com/main.js",
        "line-number": 42,
        "column-number": 7,
    }
}

_REPORTING_API = [
    {
        "type": "csp-violation",
        "age": 12,
        "url": "https://app.example.com/devices",
        "body": {
            "documentURL": "https://app.example.com/devices",
            "effectiveDirective": "img-src",
            "blockedURL": "https://cdn.evil.test/p.png",
            "sourceFile": "https://app.example.com/asset.js",
            "lineNumber": 100,
            "columnNumber": 3,
            "disposition": "report",
        },
    }
]


class TestCspReportEndpoint:
    def test_legacy_csp_report_shape_204(self, security_client: TestClient) -> None:
        response = security_client.post(
            "/security/csp-report",
            json=_LEGACY_REPORT,
            headers={"Content-Type": "application/csp-report"},
        )
        assert response.status_code == 204

    def test_reporting_api_shape_204(self, security_client: TestClient) -> None:
        response = security_client.post(
            "/security/csp-report",
            json=_REPORTING_API,
            headers={"Content-Type": "application/reports+json"},
        )
        assert response.status_code == 204

    def test_increments_prom_counter_with_directive(self, security_client: TestClient) -> None:
        before = security_module.csp_violations_total.labels(directive="script-src")._value.get()
        security_client.post("/security/csp-report", json=_LEGACY_REPORT)
        after = security_module.csp_violations_total.labels(directive="script-src")._value.get()
        assert after - before == 1

    def test_per_ip_rate_limit_429_after_10_per_minute(self, security_client: TestClient) -> None:
        for _ in range(10):
            r = security_client.post("/security/csp-report", json=_LEGACY_REPORT)
            assert r.status_code == 204
        r = security_client.post("/security/csp-report", json=_LEGACY_REPORT)
        assert r.status_code == 429

    def test_no_auth_required(self, security_client: TestClient) -> None:
        # Browsers don't send credentials on report POSTs by design.
        response = security_client.post("/security/csp-report", json=_LEGACY_REPORT)
        assert response.status_code != 401
        assert response.status_code != 403

    def test_malformed_json_204_not_500(self, security_client: TestClient) -> None:
        response = security_client.post(
            "/security/csp-report",
            content=b"this is not json",
            headers={"Content-Type": "application/csp-report"},
        )
        # Logged + dropped, never crashes.
        assert response.status_code == 204
