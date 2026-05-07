"""Sprint 23 Phase B: VNet integration + private endpoint static checks.

Validates the deploy artifacts only -- no live Azure calls. Mirrors the
Phase A test style (REPO_ROOT, comment-stripped bicep_code class attrs).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BICEP_DIR = REPO_ROOT / "deploy" / "azure" / "bicep"
MODULES = BICEP_DIR / "modules"


def _strip_comments(text: str) -> str:
    """Remove `// ...` line comments so assertions don't false-match on docs."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


class TestNetworkModule:
    bicep_text = (MODULES / "network.bicep").read_text()
    bicep_code = _strip_comments(bicep_text)

    def test_module_exists(self) -> None:
        assert (MODULES / "network.bicep").is_file()

    def test_vnet_address_space(self) -> None:
        assert "10.10.0.0/16" in self.bicep_code

    def test_three_subnets_with_expected_cidrs(self) -> None:
        assert "'aca-infra'" in self.bicep_code
        assert "10.10.0.0/23" in self.bicep_code
        assert "'pe'" in self.bicep_code
        assert "10.10.2.0/27" in self.bicep_code
        assert "'mgmt'" in self.bicep_code
        assert "10.10.3.0/27" in self.bicep_code

    def test_aca_subnet_delegated(self) -> None:
        assert "Microsoft.App/environments" in self.bicep_code

    def test_pe_subnet_disables_pe_network_policies(self) -> None:
        assert "privateEndpointNetworkPolicies: 'Disabled'" in self.bicep_code

    def test_outputs(self) -> None:
        for output_name in ("id", "name", "acaSubnetId", "peSubnetId", "mgmtSubnetId"):
            assert f"output {output_name} string" in self.bicep_code

    def test_aca_nsg_allows_azurecloud_443(self) -> None:
        # AzureCloud:443 inbound is the documented requirement for
        # VNet-integrated ACA control plane access.
        assert "'AzureCloud'" in self.bicep_code
        assert "'443'" in self.bicep_code


class TestPrivateEndpointModule:
    bicep_code = _strip_comments((MODULES / "private-endpoint.bicep").read_text())

    def test_module_exists(self) -> None:
        assert (MODULES / "private-endpoint.bicep").is_file()

    def test_creates_pe_dns_zone_link_and_zone_group(self) -> None:
        assert "Microsoft.Network/privateEndpoints@" in self.bicep_code
        assert "Microsoft.Network/privateDnsZones@" in self.bicep_code
        assert "virtualNetworkLinks@" in self.bicep_code
        assert "privateDnsZoneGroups@" in self.bicep_code


class TestKeyVaultPublicAccessToggle:
    bicep_code = _strip_comments((MODULES / "keyvault.bicep").read_text())

    def test_param_present(self) -> None:
        assert "param disablePublicNetworkAccess bool = false" in self.bicep_code

    def test_public_network_access_uses_param(self) -> None:
        assert (
            "publicNetworkAccess: disablePublicNetworkAccess ? 'Disabled' : 'Enabled'"
            in self.bicep_code
        )

    def test_network_acls_default_action_uses_param(self) -> None:
        assert "defaultAction: 'Deny'" in self.bicep_code
        assert "defaultAction: 'Allow'" in self.bicep_code


class TestPostgresPublicAccessToggle:
    bicep_code = _strip_comments((MODULES / "postgres.bicep").read_text())

    def test_param_present(self) -> None:
        assert "param disablePublicNetworkAccess bool = false" in self.bicep_code

    def test_firewall_rule_is_conditional(self) -> None:
        # The AllowAllAzureIPs firewall rule must NOT deploy when public
        # access is off (the API would reject it anyway).
        assert (
            "if (!disablePublicNetworkAccess)" in self.bicep_code
            and "AllowAllAzureIPs" in self.bicep_code
        )

    def test_id_output(self) -> None:
        # Required so the PE module can target it.
        assert "output id string = pg.id" in self.bicep_code


class TestAcrPremiumToggle:
    bicep_code = _strip_comments((MODULES / "acr.bicep").read_text())

    def test_param_present(self) -> None:
        assert "param enablePrivateEndpoint bool = false" in self.bicep_code

    def test_sku_swaps_to_premium(self) -> None:
        assert "name: enablePrivateEndpoint ? 'Premium' : 'Basic'" in self.bicep_code

    def test_public_access_stays_enabled(self) -> None:
        # ADR-017 explicitly defers closing public ACR to Sprint 24+ so
        # GHA hosted-runner pushes keep working.
        assert "publicNetworkAccess: 'Enabled'" in self.bicep_code


class TestAcaEnvVnetIntegration:
    bicep_code = _strip_comments((MODULES / "container-apps-env.bicep").read_text())

    def test_param_present(self) -> None:
        assert "param infrastructureSubnetId string = ''" in self.bicep_code

    def test_vnet_config_is_conditional(self) -> None:
        assert "empty(infrastructureSubnetId) ? null" in self.bicep_code
        assert "infrastructureSubnetId: infrastructureSubnetId" in self.bicep_code


class TestWorkloadWiring:
    bicep_code = _strip_comments((BICEP_DIR / "workload.bicep").read_text())

    def test_feature_flag_params(self) -> None:
        assert "param enableVnetIntegration bool = false" in self.bicep_code
        assert "param disablePublicNetworkAccess bool = false" in self.bicep_code

    def test_network_module_conditionally_instantiated(self) -> None:
        assert (
            "module network 'modules/network.bicep' = if (enableVnetIntegration)" in self.bicep_code
        )

    def test_three_private_endpoints_gated_on_effective_flag(self) -> None:
        for pe in ("kvPrivateEndpoint", "postgresPrivateEndpoint", "acrPrivateEndpoint"):
            assert pe in self.bicep_code
        # PEs gate on the SAFETY-COERCED effective value so the brick
        # scenario (DPNA=true with VNet=false) doesn't silently bring down
        # the env -- in that case effective=false and PEs simply don't deploy.
        assert "if (disablePublicNetworkAccessEffective)" in self.bicep_code

    def test_effective_var_coerces_to_false_without_vnet(self) -> None:
        # The whole point of the guard.
        expected = (
            "var disablePublicNetworkAccessEffective = "
            "enableVnetIntegration && disablePublicNetworkAccess"
        )
        assert expected in self.bicep_code

    def test_effective_value_exposed_as_output(self) -> None:
        expected = (
            "output disablePublicNetworkAccessEffective bool = disablePublicNetworkAccessEffective"
        )
        assert expected in self.bicep_code

    def test_aca_env_receives_subnet_when_vnet_enabled(self) -> None:
        assert (
            "infrastructureSubnetId: enableVnetIntegration ? network!.outputs.acaSubnetId : ''"
            in self.bicep_code
        )

    def test_modules_propagate_disable_public_flag(self) -> None:
        # KV and Postgres modules each receive the EFFECTIVE (safety-coerced) flag.
        kv_block_start = self.bicep_code.index("module kv 'modules/keyvault.bicep'")
        kv_block = self.bicep_code[kv_block_start : kv_block_start + 600]
        assert "disablePublicNetworkAccess: disablePublicNetworkAccessEffective" in kv_block

        pg_block_start = self.bicep_code.index("module postgres 'modules/postgres.bicep'")
        pg_block = self.bicep_code[pg_block_start : pg_block_start + 400]
        assert "disablePublicNetworkAccess: disablePublicNetworkAccessEffective" in pg_block

        acr_block_start = self.bicep_code.index("module acr 'modules/acr.bicep'")
        acr_block = self.bicep_code[acr_block_start : acr_block_start + 300]
        assert "enablePrivateEndpoint: disablePublicNetworkAccessEffective" in acr_block


class TestMainBicepWiring:
    main_code = _strip_comments((BICEP_DIR / "main.bicep").read_text())
    param_code = _strip_comments((BICEP_DIR / "main.bicepparam").read_text())

    def test_main_declares_flags(self) -> None:
        assert "param enableVnetIntegration bool = false" in self.main_code
        assert "param disablePublicNetworkAccess bool = false" in self.main_code

    def test_main_passes_flags_to_workload(self) -> None:
        assert "enableVnetIntegration: enableVnetIntegration" in self.main_code
        assert "disablePublicNetworkAccess: disablePublicNetworkAccess" in self.main_code

    def test_bicepparam_reads_env_vars(self) -> None:
        assert "AZURE_ENABLE_VNET" in self.param_code
        assert "AZURE_DISABLE_PUBLIC_NETWORK_ACCESS" in self.param_code

    def test_bicepparam_defaults_false(self) -> None:
        # Both flags must default to 'false' so envs not on the corporate
        # policy keep the Sprint 22 deploy path.
        assert "'AZURE_ENABLE_VNET', 'false'" in self.param_code
        assert "'AZURE_DISABLE_PUBLIC_NETWORK_ACCESS', 'false'" in self.param_code

    def test_main_propagates_effective_output(self) -> None:
        # Audit gap: the safety-coerced value is exposed in workload.bicep
        # but the runbook tells operators to query it via `az deployment sub
        # show --query 'properties.outputs.disablePublicNetworkAccessEffective.value'`,
        # which only works if main.bicep re-exports the workload output.
        assert "output disablePublicNetworkAccessEffective bool" in self.main_code
        assert "workload.outputs.disablePublicNetworkAccessEffective" in self.main_code


class TestNetworkCheckScript:
    script = REPO_ROOT / "scripts" / "azd-network-check.sh"
    text = script.read_text()

    def test_script_exists_and_executable(self) -> None:
        assert self.script.is_file()
        import os

        assert os.access(self.script, os.X_OK), "azd-network-check.sh must be executable"

    def test_noops_when_flags_off(self) -> None:
        # The early-exit guard is the contract that keeps this safe to
        # leave wired into postdeploy on Sprint 22 envs.
        assert "AZURE_ENABLE_VNET:-false" in self.text
        assert "AZURE_DISABLE_PUBLIC_NETWORK_ACCESS:-false" in self.text
        assert "exit 0" in self.text

    def test_uses_python_socket_resolution(self) -> None:
        # Per repo memory: nslookup isn't always present in the slim
        # Container Apps base image, but Python is.
        assert "socket.gethostbyname" in self.text

    def test_resolves_inside_via_containerapp_exec(self) -> None:
        assert "az containerapp exec" in self.text


class TestPreflightRegistersNetwork:
    text = (REPO_ROOT / "scripts" / "azd-preflight.sh").read_text()

    def test_microsoft_network_added_when_vnet_enabled(self) -> None:
        assert "AZURE_ENABLE_VNET:-false" in self.text
        assert "Microsoft.Network" in self.text


class TestPostdeployHookWiring:
    yaml_text = (REPO_ROOT / "azure.yaml").read_text()

    def test_postdeploy_invokes_network_check(self) -> None:
        assert "scripts/azd-network-check.sh" in self.yaml_text


class TestGhaSmokeStep:
    workflow_text = (REPO_ROOT / ".github" / "workflows" / "deploy-azure.yml").read_text()

    def test_network_smoke_step_present_and_gated(self) -> None:
        assert "Network smoke (VNet + private endpoints)" in self.workflow_text
        assert "AZURE_ENABLE_VNET == 'true'" in self.workflow_text
        assert "scripts/azd-network-check.sh" in self.workflow_text


class TestCutoverRunbook:
    runbook = REPO_ROOT / "docs" / "runbooks" / "sprint-23-network-cutover.md"

    def test_runbook_exists(self) -> None:
        assert self.runbook.is_file()

    def test_documents_immutability_and_purge(self) -> None:
        text = self.runbook.read_text()
        # The "why this is a cutover" callout is the most important part of
        # the doc -- without it on-call may try in-place flag flips.
        assert "immutable" in text.lower()
        assert "azd down --purge --force" in text
        assert "AZURE_ENABLE_VNET" in text
        assert "AZURE_DISABLE_PUBLIC_NETWORK_ACCESS" in text
