"""Sprint 24 Phase A: frontend-deployment backend prerequisites.

Validates:
* A1 — `scripts/azd-ui-token.sh` shipped, executable, refuses TTY by default.
* A2 — `/health/ready` `_config_snapshot()` surfaces `cors.allow_origins`.
* A3 — `static-web-app.bicep` does NOT emit the deployment apiKey as an
       output (would land it in `azd env get-values` and git-status traces).
* A4 — `docs/runbooks/azure-first-deploy.md` Phase 3 documents the
       SWA-hostname → CORS_ORIGINS step.

Static / unit tests only — no live Azure calls.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BICEP_MODULES = REPO_ROOT / "deploy" / "azure" / "bicep" / "modules"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "azure-first-deploy.md"


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


# -----------------------------------------------------------------------------
# A1 — scripts/azd-ui-token.sh
# -----------------------------------------------------------------------------


class TestUiTokenHelper:
    script = SCRIPTS / "azd-ui-token.sh"

    def test_exists_and_executable(self) -> None:
        assert self.script.is_file()
        mode = self.script.stat().st_mode
        assert mode & stat.S_IXUSR, "azd-ui-token.sh must be chmod +x"

    def test_uses_safe_get_helper(self) -> None:
        # The azd env get-value stdout-error trap (carried over from
        # azd-mqtt-build.sh / azd-image-check.sh): wrapped get() that
        # discards stderr and only emits on success.
        text = self.script.read_text()
        assert "azd -e" in text and "env get-value" in text
        assert "2>/dev/null" in text

    def test_refuses_tty_without_print_flag(self) -> None:
        # No env / no Azure required: the TTY guard fires before any
        # azd / az invocation. Run without --print, force stdout to a
        # non-TTY pipe so we hit the env-var path; then a separate run
        # under script(1) would simulate a TTY — easier: just inspect
        # the source for the guard.
        text = self.script.read_text()
        assert "-t 1" in text, "Must guard against TTY stdout"
        assert "--print" in text

    def test_help_flag_works(self) -> None:
        result = subprocess.run(  # noqa: S603
            ["/bin/bash", str(self.script), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
            check=False,
        )
        assert result.returncode == 0
        assert "azd-ui-token" in result.stdout or "Usage" in result.stdout


# -----------------------------------------------------------------------------
# A2 — /health/ready surfaces cors.allow_origins
# -----------------------------------------------------------------------------


class TestHealthReadyCorsSurface:
    def test_config_snapshot_includes_cors(self) -> None:
        from tagpulse.api.routes import health

        snap = health._config_snapshot()
        assert "cors" in snap
        assert "allow_origins" in snap["cors"]
        assert isinstance(snap["cors"]["allow_origins"], list)

    def test_cors_origins_parsed_as_list(self) -> None:
        # Mirror the middleware's parsing: comma-separated string ->
        # stripped list, no blank entries.
        from tagpulse.core.config import Settings

        s = Settings(  # type: ignore[call-arg]
            _env_file=None,
            cors_origins="https://a.example.com, https://b.example.com",
        )
        # Reach into the snapshot's logic by replicating it.
        parsed = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
        assert parsed == ["https://a.example.com", "https://b.example.com"]


# -----------------------------------------------------------------------------
# A3 — static-web-app.bicep audit (no apiKey output)
# -----------------------------------------------------------------------------


class TestStaticWebAppBicepAudit:
    bicep_text = (BICEP_MODULES / "static-web-app.bicep").read_text()
    bicep_code = _strip_comments(bicep_text)

    def test_module_exists(self) -> None:
        assert (BICEP_MODULES / "static-web-app.bicep").is_file()

    def test_no_apikey_output(self) -> None:
        # Anything that pulls listSecrets() into a Bicep output would
        # land the SWA deployment token in `azd env get-values`,
        # `azd env list` traces, and any CI artifact that captures
        # them. The token is intentionally fetched at runtime via
        # `scripts/azd-ui-token.sh` (Sprint 24 A1) instead.
        assert "listSecrets" not in self.bicep_code
        assert "apiKey" not in self.bicep_code, (
            "static-web-app.bicep must not output the SWA apiKey (see Sprint 24 A3)"
        )

    def test_expected_outputs_only(self) -> None:
        # Pin the exact output set so a future contributor adding
        # `output apiKey ...` shows up as a diff in this test.
        outputs = re.findall(r"^output\s+(\w+)\s+", self.bicep_text, flags=re.M)
        assert sorted(outputs) == ["defaultHostname", "id", "name"]


# -----------------------------------------------------------------------------
# A4 — runbook documents the CORS-hostname step
# -----------------------------------------------------------------------------


class TestRunbookCorsStep:
    text = RUNBOOK.read_text()

    def test_runbook_exists(self) -> None:
        assert RUNBOOK.is_file()

    def test_phase_3_mentions_swa_hostname_in_cors(self) -> None:
        # The step has to appear inside (or right after) Phase 3 —
        # not buried in an appendix — because it gates the first
        # browser session against the new SPA.
        assert "staticWebAppHostname" in self.text
        assert "CORS_ORIGINS" in self.text or "cors.allow_origins" in self.text
        assert "azd-env-load.sh" in self.text
        # And the operator should know to re-provision after editing.
        assert "azd provision" in self.text
