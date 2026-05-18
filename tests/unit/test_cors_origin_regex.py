"""CORS allow-origin regex support (Azure Static Web App preview slots).

Azure Static Web App preview deployments use per-PR hostnames like

    https://<basename>-42.centralus.7.azurestaticapps.net

which cannot be enumerated ahead of time in the comma-separated
``CORS_ORIGINS`` allow-list. This module covers the fix:

* Settings exposes ``cors_origin_regex`` (env var ``CORS_ORIGIN_REGEX``)
  with a compile-time syntax check (fail-fast at boot, not mid-request).
* ``api.main`` forwards it to Starlette ``CORSMiddleware`` as
  ``allow_origin_regex`` (empty string -> ``None``).
* ``/health/ready`` ``_config_snapshot()`` surfaces it so operators can
  verify the regex landed on the deployed revision without exec'ing in.
* Bicep wiring: ``modules/container-app.bicep`` accepts
  ``corsOriginRegex`` and emits the ``CORS_ORIGIN_REGEX`` env var;
  ``workload.bicep`` auto-derives the regex from the SWA default
  hostname; ``main.bicepparam`` reads ``CORS_ORIGIN_REGEX`` for an
  operator override.

Unit-level only — no live Azure calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
BICEP_DIR = REPO_ROOT / "deploy" / "azure" / "bicep"


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------


class TestSettingsCorsOriginRegex:
    def test_default_is_empty_string(self) -> None:
        from tagpulse.core.config import Settings

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.cors_origin_regex == ""

    def test_accepts_valid_regex(self) -> None:
        from tagpulse.core.config import Settings

        pattern = r"^https://example-app(-\d+)?\.7\.azurestaticapps\.net$"
        s = Settings(  # type: ignore[call-arg]
            _env_file=None,
            cors_origin_regex=pattern,
        )
        assert s.cors_origin_regex == pattern
        # The validator must have left it compile-able.
        re.compile(s.cors_origin_regex)

    def test_rejects_invalid_regex(self) -> None:
        from tagpulse.core.config import Settings

        with pytest.raises(ValidationError) as excinfo:
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                cors_origin_regex="^https://[unclosed",
            )
        # The Pydantic ValidationError wraps the re.error message.
        assert "cors_origin_regex" in str(excinfo.value)

    def test_env_var_wires_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tagpulse.core.config import Settings

        pattern = r"^https://my-swa(-\d+)?\.centralus\.7\.azurestaticapps\.net$"
        monkeypatch.setenv("CORS_ORIGIN_REGEX", pattern)
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.cors_origin_regex == pattern


# -----------------------------------------------------------------------------
# /health/ready snapshot
# -----------------------------------------------------------------------------


class TestHealthReadySnapshot:
    def test_snapshot_includes_allow_origin_regex_key(self) -> None:
        from tagpulse.api.routes import health

        snap = health._config_snapshot()
        assert "cors" in snap
        assert "allow_origin_regex" in snap["cors"], (
            "Operators rely on /health/ready to verify the regex landed on "
            "the deployed revision without exec'ing into the container."
        )

    def test_snapshot_emits_none_when_unset(self) -> None:
        # The default Settings has cors_origin_regex="" which the snapshot
        # converts to None so it is unambiguous in JSON.
        from tagpulse.api.routes import health
        from tagpulse.core import config as config_mod

        original = config_mod.settings.cors_origin_regex
        config_mod.settings.cors_origin_regex = ""
        try:
            snap = health._config_snapshot()
            assert snap["cors"]["allow_origin_regex"] is None
        finally:
            config_mod.settings.cors_origin_regex = original

    def test_snapshot_emits_pattern_when_set(self) -> None:
        from tagpulse.api.routes import health
        from tagpulse.core import config as config_mod

        pattern = r"^https://my-swa(-\d+)?\.7\.azurestaticapps\.net$"
        original = config_mod.settings.cors_origin_regex
        config_mod.settings.cors_origin_regex = pattern
        try:
            snap = health._config_snapshot()
            assert snap["cors"]["allow_origin_regex"] == pattern
        finally:
            config_mod.settings.cors_origin_regex = original


# -----------------------------------------------------------------------------
# CORSMiddleware preflight integration
# -----------------------------------------------------------------------------


def _build_test_app(*, allow_origins: list[str], allow_origin_regex: str | None) -> FastAPI:
    """Construct a minimal FastAPI app with CORSMiddleware mirroring main.py.

    Done locally (not via importing api.main) so the test exercises the
    Starlette layer directly with arbitrary regexes without rebuilding the
    full app at import time.
    """

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    return app


# The hostname pattern auto-derived by workload.bicep for a SWA whose
# defaultHostname is `my-swa.7.azurestaticapps.net`.
PREVIEW_REGEX = r"^https://my-swa(-\d+)?(\.[a-z0-9-]+)?\.7\.azurestaticapps\.net$"


class TestPreflightAgainstSwaPreviewSlot:
    @pytest.mark.parametrize(
        "origin",
        [
            "https://my-swa.7.azurestaticapps.net",  # production
            "https://my-swa-1.7.azurestaticapps.net",  # preview, no region
            "https://my-swa-42.centralus.7.azurestaticapps.net",  # preview + region
        ],
    )
    def test_matching_origin_passes_preflight(self, origin: str) -> None:
        app = _build_test_app(
            allow_origins=["http://localhost:5173"],
            allow_origin_regex=PREVIEW_REGEX,
        )
        client = TestClient(app)
        resp = client.options(
            "/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == origin

    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil-swa.7.azurestaticapps.net",  # wrong basename
            "https://my-swa.7.example.com",  # wrong suffix
            "http://my-swa-42.centralus.7.azurestaticapps.net",  # http, not https
        ],
    )
    def test_non_matching_origin_rejected(self, origin: str) -> None:
        app = _build_test_app(
            allow_origins=["http://localhost:5173"],
            allow_origin_regex=PREVIEW_REGEX,
        )
        client = TestClient(app)
        resp = client.options(
            "/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        # Starlette returns 400 for disallowed preflights and omits
        # the Access-Control-Allow-Origin header.
        assert resp.headers.get("access-control-allow-origin") is None

    def test_explicit_allow_list_still_works_alongside_regex(self) -> None:
        # The dev workstation entry in allow_origins must still pass.
        app = _build_test_app(
            allow_origins=["http://localhost:5173"],
            allow_origin_regex=PREVIEW_REGEX,
        )
        client = TestClient(app)
        resp = client.options(
            "/ping",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


# -----------------------------------------------------------------------------
# Bicep wiring
# -----------------------------------------------------------------------------


class TestBicepWiring:
    def test_container_app_module_exposes_param(self) -> None:
        text = (BICEP_DIR / "modules" / "container-app.bicep").read_text()
        assert "param corsOriginRegex string" in text, (
            "container-app.bicep must accept a corsOriginRegex parameter"
        )

    def test_container_app_module_emits_env_var(self) -> None:
        text = (BICEP_DIR / "modules" / "container-app.bicep").read_text()
        assert "'CORS_ORIGIN_REGEX'" in text and "value: corsOriginRegex" in text, (
            "container-app.bicep must emit the CORS_ORIGIN_REGEX env var "
            "so Settings.cors_origin_regex picks it up"
        )

    def test_workload_passes_regex_to_api_app(self) -> None:
        text = (BICEP_DIR / "workload.bicep").read_text()
        assert "corsOriginRegex:" in text, (
            "workload.bicep must pass corsOriginRegex to the apiApp module"
        )

    def test_workload_auto_derives_from_swa_hostname(self) -> None:
        text = (BICEP_DIR / "workload.bicep").read_text()
        # The auto-derived regex must reference the SWA hostname so it
        # adapts to per-environment SWA names without operator action.
        assert "ui.outputs.defaultHostname" in text
        # And it must be gated on corsOriginRegexOverride so operators can
        # take manual control via the CORS_ORIGIN_REGEX env var.
        assert "corsOriginRegexOverride" in text

    def test_bicepparam_reads_env_override(self) -> None:
        text = (BICEP_DIR / "main.bicepparam").read_text()
        assert "CORS_ORIGIN_REGEX" in text, (
            "main.bicepparam must expose CORS_ORIGIN_REGEX as an env "
            "override so operators can tighten/broaden the regex"
        )
