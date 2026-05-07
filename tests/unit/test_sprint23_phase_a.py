"""Sprint 23 Phase A: custom Mosquitto image + Files-less ACI tests.

Static checks against the deploy artifacts:
- docker/mosquitto.Dockerfile bases on eclipse-mosquitto and bakes the
  hardened conf + entrypoint into the image.
- docker/mosquitto.prod.conf disables anonymous access and points at the
  password file the entrypoint materialises.
- docker/mosquitto-entrypoint.sh fast-fails on missing env vars.
- deploy/azure/bicep/modules/mqtt.bicep no longer references
  Microsoft.Storage and DOES include imageRegistryCredentials (required
  for ACI managed-identity ACR pull).
- scripts/azd-image-check.sh probes all four ACR repos.
- scripts/azd-mqtt-build.sh exists and is wired into azure.yaml's
  preprovision hook.
- The obsolete scripts/azd-bootstrap-mqtt.sh has been removed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestMosquittoImage:
    def test_dockerfile_exists_and_uses_eclipse_mosquitto_base(self) -> None:
        dockerfile = REPO_ROOT / "docker" / "mosquitto.Dockerfile"
        assert dockerfile.is_file(), "docker/mosquitto.Dockerfile missing"
        content = dockerfile.read_text()
        assert "FROM eclipse-mosquitto" in content
        assert "COPY docker/mosquitto.prod.conf" in content
        assert "COPY docker/mosquitto-entrypoint.sh" in content
        assert "ENTRYPOINT" in content

    def test_prod_conf_disables_anonymous_and_uses_password_file(self) -> None:
        conf = (REPO_ROOT / "docker" / "mosquitto.prod.conf").read_text()
        assert "allow_anonymous false" in conf
        assert "password_file /mosquitto/config/mosquitto.passwd" in conf
        assert "listener 1883" in conf

    def test_dev_conf_unchanged(self) -> None:
        # Local docker-compose still uses the anonymous dev conf so that
        # `docker compose up` works without env wiring.
        conf = (REPO_ROOT / "docker" / "mosquitto.conf").read_text()
        assert "allow_anonymous true" in conf

    def test_entrypoint_fast_fails_on_missing_env(self) -> None:
        entrypoint = (REPO_ROOT / "docker" / "mosquitto-entrypoint.sh").read_text()
        assert "MOSQUITTO_USERNAME" in entrypoint
        assert "MOSQUITTO_PASSWORD" in entrypoint
        assert "exit 1" in entrypoint
        assert "mosquitto_passwd" in entrypoint


class TestMqttBicep:
    bicep_path = REPO_ROOT / "deploy" / "azure" / "bicep" / "modules" / "mqtt.bicep"
    bicep = bicep_path.read_text()
    # Strip comments so context-only mentions in the file header don't trip
    # the negative assertions below.
    bicep_code = "\n".join(
        line for line in bicep.splitlines() if not line.lstrip().startswith("//")
    )

    def test_no_storage_account(self) -> None:
        # Sprint 23 Phase A: corporate `Modify`-mode policy reverts
        # allowSharedKeyAccess and ACI cannot mount Azure Files with a
        # managed identity. The whole storage path is gone.
        assert "Microsoft.Storage" not in self.bicep_code
        assert "azureFile" not in self.bicep_code
        assert "fileService" not in self.bicep_code
        assert "volumeMounts" not in self.bicep_code

    def test_uses_acr_image_via_managed_identity(self) -> None:
        assert "imageRegistryCredentials" in self.bicep_code
        assert "userAssignedIdentityId" in self.bicep_code
        assert "tagpulse-mqtt" in self.bicep_code

    def test_supports_placeholder_for_first_provision(self) -> None:
        # Same first-provision pattern as the ACA modules — first `azd up`
        # uses a public placeholder so the deploy can finish before the
        # custom image is in ACR.
        assert "useImagePlaceholders" in self.bicep_code
        assert "aci-helloworld" in self.bicep_code


class TestImageCheckScript:
    script = (REPO_ROOT / "scripts" / "azd-image-check.sh").read_text()

    def test_probes_all_four_repos(self) -> None:
        # tagpulse-mqtt was added in Sprint 23 — must be in the canary list
        # alongside api/worker/migrations, otherwise placeholders flip off
        # before the broker image exists.
        for repo in ("tagpulse-api", "tagpulse-worker", "tagpulse-migrations", "tagpulse-mqtt"):
            assert repo in self.script, f"image-check missing repo {repo}"


class TestMqttBuildHook:
    def test_build_script_exists_and_is_executable(self) -> None:
        script = REPO_ROOT / "scripts" / "azd-mqtt-build.sh"
        assert script.is_file()
        # `az acr build` is the cloud-build path — no local Docker daemon
        # needed in CI / on locked-down workstations.
        content = script.read_text()
        assert "az acr build" in content
        assert "tagpulse-mqtt" in content
        assert "docker/mosquitto.Dockerfile" in content

    def test_wired_into_preprovision_hook(self) -> None:
        azure_yaml = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text())
        preprovision_run = azure_yaml["hooks"]["preprovision"]["run"]
        assert "./scripts/azd-mqtt-build.sh" in preprovision_run
        # Must come AFTER kv-recover (recovery may flip AZURE_KV_NAME_SUFFIX)
        # and BEFORE image-check (which gates placeholder toggling on the
        # mqtt image being present in ACR).
        kv_pos = preprovision_run.index("./scripts/azd-kv-recover.sh")
        mqtt_pos = preprovision_run.index("./scripts/azd-mqtt-build.sh")
        check_pos = preprovision_run.index("./scripts/azd-image-check.sh")
        assert kv_pos < mqtt_pos < check_pos


class TestObsoleteBootstrapScriptRemoved:
    def test_azd_bootstrap_mqtt_script_gone(self) -> None:
        # Sprint 23 Phase A obsoleted the manual Files-share seeding step.
        path = REPO_ROOT / "scripts" / "azd-bootstrap-mqtt.sh"
        assert not path.exists(), (
            "scripts/azd-bootstrap-mqtt.sh should have been removed in Sprint 23 Phase A"
        )
