"""Sprint 22 Phase B: container/migration pipeline + worker split tests.

Note: the runtime ``lifespan`` integration (workers_inline=False skips
worker registrations) is exercised in a separate integration test that
needs a live DB. At unit-test scope we verify (a) the Settings flag
exists with the right default and (b) the static deploy artifacts
(Dockerfile, k8s Job, GH Actions workflow, Helm chart) are well-formed
and consume the flag correctly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from tagpulse.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


# -----------------------------------------------------------------------------
# B1 — workers_inline Settings flag
# -----------------------------------------------------------------------------


class TestWorkersInlineSetting:
    def test_workers_inline_default_true(self) -> None:
        # Backwards-compat: dev / single-container deployments keep working.
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.workers_inline is True

    def test_workers_inline_can_be_disabled(self) -> None:
        s = Settings(_env_file=None, workers_inline=False)  # type: ignore[call-arg]
        assert s.workers_inline is False


# -----------------------------------------------------------------------------
# B1 — main.py honours the workers_inline flag (static check on the source)
# -----------------------------------------------------------------------------


class TestLifespanGate:
    def test_lifespan_gates_workers_on_settings(self) -> None:
        main_src = (REPO_ROOT / "src" / "tagpulse" / "api" / "main.py").read_text()
        # The lifespan body must branch on settings.workers_inline so the API
        # container can opt out of starting MQTT + background workers.
        assert "if settings.workers_inline" in main_src
        # event_bus + usage_meter must remain unconditional (HTTP routes need them).
        assert "app.state.event_bus = event_bus" in main_src
        assert "app.state.usage_meter = usage_meter" in main_src


# -----------------------------------------------------------------------------
# B1 — Dockerfile multi-target shape
# -----------------------------------------------------------------------------


class TestDockerfileTargets:
    def test_dockerfile_declares_three_targets(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        for target in ("AS api", "AS worker", "AS migrations"):
            assert target in dockerfile, f"Dockerfile missing stage `{target}`"

    def test_api_target_disables_workers_inline(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        # The api stage must default WORKERS_INLINE=false so a single-image
        # rollout doesn't double-subscribe MQTT.
        api_stage = dockerfile.split("AS api", 1)[1].split("FROM ", 1)[0]
        assert "WORKERS_INLINE=false" in api_stage

    def test_worker_target_enables_workers_inline(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        worker_stage = dockerfile.split("AS worker", 1)[1].split("FROM ", 1)[0]
        assert "WORKERS_INLINE=true" in worker_stage

    def test_migrations_target_runs_alembic(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        migrations_stage = dockerfile.split("AS migrations", 1)[1].split("FROM ", 1)[0]
        assert "alembic" in migrations_stage.lower()


# -----------------------------------------------------------------------------
# B2 — k8s migrations Job manifest
# -----------------------------------------------------------------------------


class TestMigrationsJobManifest:
    def test_manifest_exists_and_parses(self) -> None:
        manifest_path = REPO_ROOT / "deploy" / "common" / "migrations-job.yaml"
        assert manifest_path.exists(), "deploy/common/migrations-job.yaml missing"
        doc = yaml.safe_load(manifest_path.read_text())
        assert doc["kind"] == "Job"
        assert doc["spec"]["template"]["spec"]["restartPolicy"] == "OnFailure"
        # Must carry the migrations image so a misconfigured deploy fails loudly.
        container = doc["spec"]["template"]["spec"]["containers"][0]
        assert "tagpulse-migrations" in container["image"]


# -----------------------------------------------------------------------------
# B3 — GitHub Actions build-and-push workflow
# -----------------------------------------------------------------------------


class TestBuildAndPushWorkflow:
    def test_workflow_exists_with_matrix(self) -> None:
        wf_path = REPO_ROOT / ".github" / "workflows" / "build-and-push.yml"
        assert wf_path.exists()
        wf = yaml.safe_load(wf_path.read_text())
        matrix = wf["jobs"]["build"]["strategy"]["matrix"]["component"]
        assert set(matrix) == {"api", "worker", "migrations"}


# -----------------------------------------------------------------------------
# B4 — Helm chart shape
# -----------------------------------------------------------------------------


class TestHelmChart:
    CHART_DIR = REPO_ROOT / "deploy" / "common" / "helm" / "tagpulse"

    def test_chart_yaml_present(self) -> None:
        chart = yaml.safe_load((self.CHART_DIR / "Chart.yaml").read_text())
        assert chart["name"] == "tagpulse"
        assert chart["apiVersion"] == "v2"

    def test_required_templates_present(self) -> None:
        templates = self.CHART_DIR / "templates"
        for name in (
            "_helpers.tpl",
            "api-deployment.yaml",
            "api-service.yaml",
            "worker-deployment.yaml",
            "migrations-job.yaml",
            "serviceaccount.yaml",
            "poddisruptionbudget.yaml",
            "servicemonitor.yaml",
        ):
            assert (templates / name).exists(), f"missing template: {name}"

    def test_values_yaml_parses(self) -> None:
        values = yaml.safe_load((self.CHART_DIR / "values.yaml").read_text())
        # Sprint 22 A1 contract — must be present so the strict-mode validator
        # has a value to read in production overlays.
        assert values["environment"] in {"dev", "staging", "production"}
        # Worker / api defaults must be wired.
        assert values["api"]["replicaCount"] >= 1
        assert values["worker"]["replicaCount"] >= 1
        assert values["migrations"]["enabled"] is True

    def test_migrations_job_template_has_helm_hook(self) -> None:
        # Pre-rollout hook is non-negotiable; without it api/worker can roll
        # before the schema is upgraded and crash on startup.
        template = (self.CHART_DIR / "templates" / "migrations-job.yaml").read_text()
        assert "helm.sh/hook" in template
        assert "pre-install" in template
        assert "pre-upgrade" in template

    @pytest.mark.skipif(
        os.environ.get("TAGPULSE_HELM_RENDER_CHECK") != "1",
        reason="set TAGPULSE_HELM_RENDER_CHECK=1 to run `helm template` "
        "(requires a working helm CLI; opt-in to avoid snap/auth hangs in CI)",
    )
    def test_helm_template_renders(self) -> None:
        import subprocess

        cmd = [
            "helm",
            "template",
            "test",
            str(self.CHART_DIR),
            "--set",
            "environment=dev",
            "--set",
            "config.strictMigrationCheck=false",
        ]
        result = subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # Sanity: rendered output should contain at least three resources.
        assert result.stdout.count("kind: Deployment") >= 2
        assert "kind: Job" in result.stdout
