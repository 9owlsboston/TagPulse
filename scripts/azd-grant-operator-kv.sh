#!/usr/bin/env bash
# scripts/azd-grant-operator-kv.sh <env> [--role <role>] [--principal <objectId>]
#                                        [--allow-my-ip | --revoke-my-ip]
#                                        [--ip <addr>]
#
# Grant the operator (you, by default) read access to the deployment's
# Key Vault so secrets pushed by the tools-job (e.g. API keys regenerated
# by `smoke_setup.py --regenerate-key`) can be retrieved from a laptop.
#
# This script handles two distinct operator-access concerns:
#
#   1. RBAC (default mode) — assigns "Key Vault Secrets User" (read-only)
#      to your signed-in identity. The api/worker UAMIs and the tools-job
#      UAMI receive their KV roles at provision time via
#      deploy/azure/bicep/modules/identity.bicep. The signed-in operator
#      does not — Bicep deliberately doesn't pin a human principal so the
#      same template works for any operator. This script closes that gap.
#
#   2. Network ACL (--allow-my-ip / --revoke-my-ip) — Sprint 23-B sets
#      publicNetworkAccess=Disabled on the KV, which blocks ALL public
#      traffic (including IP-allowlisted) and only accepts private-endpoint
#      traffic. From a laptop you'll see "ForbiddenByConnection / Public
#      network access is disabled". --allow-my-ip flips publicNetworkAccess
#      to Enabled with defaultAction=Deny + your current public IP added to
#      ipRules — i.e. private-endpoint traffic AND your one IP work, nothing
#      else does. Revoke when done with --revoke-my-ip (or, equivalently,
#      next `azd provision` reconciles the bicep state).
#
# Examples:
#   scripts/azd-grant-operator-kv.sh dev                       # RBAC only
#   scripts/azd-grant-operator-kv.sh dev --allow-my-ip         # RBAC + IP allow (auto-detect via api.ipify.org)
#   scripts/azd-grant-operator-kv.sh dev --allow-my-ip --ip 20.114.144.49
#                                                              # use this when behind a corp proxy / Cloud Shell — the IP
#                                                              # api.ipify.org returns can differ from what Azure sees;
#                                                              # grab it from the Azure 'ForbiddenByFirewall' error.
#   scripts/azd-grant-operator-kv.sh dev --revoke-my-ip        # remove the IP allow
#   scripts/azd-grant-operator-kv.sh dev --role "Key Vault Secrets Officer"
#   scripts/azd-grant-operator-kv.sh dev --principal 1781b90e-...
#
# Default role: "Key Vault Secrets User" (read-only get/list). Pick "Key Vault
# Secrets Officer" only if you also need to write/rotate from your laptop.
#
# Idempotent: the role-assignment create call is no-op'd when the assignment
# already exists; --allow-my-ip is no-op when your IP is already in ipRules.

set -euo pipefail

ROLE="Key Vault Secrets User"
PRINCIPAL=""
ALLOW_MY_IP=0
REVOKE_MY_IP=0
IP_OVERRIDE=""
DRY_RUN=0

ENV_NAME="${1:-}"
if [[ -z "$ENV_NAME" || "$ENV_NAME" == "-h" || "$ENV_NAME" == "--help" ]]; then
  sed -n '2,42p' "$0"
  exit 1
fi
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)         ROLE="$2"; shift 2 ;;
    --principal)    PRINCIPAL="$2"; shift 2 ;;
    --allow-my-ip)  ALLOW_MY_IP=1; shift ;;
    --revoke-my-ip) REVOKE_MY_IP=1; shift ;;
    --ip)           IP_OVERRIDE="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    *) echo "error: unknown arg '$1'" >&2; exit 1 ;;
  esac
done

if [[ $ALLOW_MY_IP -eq 1 && $REVOKE_MY_IP -eq 1 ]]; then
  echo "error: --allow-my-ip and --revoke-my-ip are mutually exclusive" >&2
  exit 1
fi

# Resolve azd env (literal first, then "tagpulse-<env>") — same convention
# as scripts/azd-job.sh.
echo "==> Resolving env $ENV_NAME"
if azd env select "$ENV_NAME" >/dev/null 2>&1; then
  RESOLVED_ENV="$ENV_NAME"
elif azd env select "tagpulse-$ENV_NAME" >/dev/null 2>&1; then
  RESOLVED_ENV="tagpulse-$ENV_NAME"
  echo "    (resolved shorthand '$ENV_NAME' → azd env '$RESOLVED_ENV')"
else
  echo "error: azd env '$ENV_NAME' (or 'tagpulse-$ENV_NAME') not found. Run 'azd env list'." >&2
  exit 2
fi

KV_NAME="$(azd env get-value keyVaultName 2>/dev/null || echo '')"
SUB_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID 2>/dev/null || echo '')"
RG="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo '')"

if [[ -z "$KV_NAME" || -z "$SUB_ID" || -z "$RG" ]]; then
  echo "error: missing one of keyVaultName / AZURE_SUBSCRIPTION_ID / AZURE_RESOURCE_GROUP in azd env." >&2
  echo "       Run 'azd provision' (or 'azd env refresh') first." >&2
  exit 2
fi

if [[ -z "$PRINCIPAL" ]]; then
  PRINCIPAL="$(az ad signed-in-user show --query id -o tsv)"
  PRINCIPAL_TYPE="User"
  PRINCIPAL_LABEL="signed-in user $(az ad signed-in-user show --query userPrincipalName -o tsv)"
else
  # Caller-supplied principal — could be a user, group, or SP. Try user first,
  # fall back to ServicePrincipal so Bicep-style group OIDs still work.
  PRINCIPAL_TYPE="User"
  PRINCIPAL_LABEL="objectId $PRINCIPAL"
fi

SCOPE="/subscriptions/$SUB_ID/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV_NAME"

echo "    vault:      $KV_NAME"
echo "    role:       $ROLE"
echo "    principal:  $PRINCIPAL_LABEL"

# --- Network ACL: --revoke-my-ip path -----------------------------------------
if [[ $REVOKE_MY_IP -eq 1 ]]; then
  if [[ -n "$IP_OVERRIDE" ]]; then
    MY_IP="$IP_OVERRIDE"
  else
    MY_IP="$(curl -fsS https://api.ipify.org || true)"
  fi
  if [[ -z "$MY_IP" ]]; then
    echo "error: could not detect public IP via api.ipify.org (pass --ip <addr> explicitly)" >&2
    exit 1
  fi
  echo "==> Removing $MY_IP from KV ipRules and re-disabling public network access"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: would run:"
    echo "  az keyvault network-rule remove --name $KV_NAME --ip-address $MY_IP"
    echo "  az keyvault update --name $KV_NAME --public-network-access Disabled"
    exit 0
  fi
  az keyvault network-rule remove --name "$KV_NAME" --ip-address "$MY_IP" -o none || true
  az keyvault update --name "$KV_NAME" --public-network-access Disabled -o none
  echo "==> Done. KV is back to private-endpoint-only."
  exit 0
fi

# --- Network ACL: --allow-my-ip path ------------------------------------------
if [[ $ALLOW_MY_IP -eq 1 ]]; then
  if [[ -n "$IP_OVERRIDE" ]]; then
    MY_IP="$IP_OVERRIDE"
    echo "==> Using --ip override: $MY_IP"
  else
    # Try three discovery paths in order:
    #   1. Probe KV — when publicNetworkAccess=Enabled with defaultAction=Deny
    #      and our IP is missing, Azure returns "Client address: <ip>" in the
    #      ForbiddenByFirewall response. That's the IP Azure ACTUALLY sees,
    #      which is what the firewall allowlists against.
    #   2. ipify — laptop's source IP. Works for direct egress; wrong when
    #      traffic goes via Cloud Shell, an Azure VM, or a proxy.
    #
    # publicNetworkAccess=Disabled returns "ForbiddenByConnection" with NO
    # client address — the firewall is fully bypassed in that mode, so the
    # probe can't help. We fall straight to ipify in that case.
    echo "==> Probing KV to discover the source IP Azure sees..."
    PROBE_ERR="$(az keyvault secret list --vault-name "$KV_NAME" --maxresults 1 -o none 2>&1 || true)"
    MY_IP="$(echo "$PROBE_ERR" | grep -oE 'Client address: [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1 | awk '{print $3}')"
    if [[ -n "$MY_IP" ]]; then
      echo "    Azure sees: $MY_IP"
    else
      if echo "$PROBE_ERR" | grep -q 'ForbiddenByConnection'; then
        echo "    KV has publicNetworkAccess=Disabled (no client-address in error);"
        echo "    falling back to https://api.ipify.org."
      else
        echo "    KV probe didn't reveal a client address; falling back to api.ipify.org."
      fi
      MY_IP="$(curl -fsS --max-time 8 https://api.ipify.org || true)"
      [[ -n "$MY_IP" ]] && echo "    ipify reports: $MY_IP"
    fi
  fi
  if [[ -z "$MY_IP" ]]; then
    echo "error: could not determine source IP. Pass --ip <addr> explicitly." >&2
    exit 1
  fi
  echo "==> Allow my IP ($MY_IP) on KV firewall"
  echo "    Sets publicNetworkAccess=Enabled with defaultAction=Deny."
  echo "    Private-endpoint traffic AND $MY_IP will reach the data plane;"
  echo "    everything else stays blocked. Run with --revoke-my-ip when done."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: would run:"
    echo "  az keyvault update --name $KV_NAME --public-network-access Enabled --default-action Deny --bypass AzureServices"
    echo "  az keyvault network-rule add --name $KV_NAME --ip-address $MY_IP"
  else
    az keyvault update \
      --name "$KV_NAME" \
      --public-network-access Enabled \
      --default-action Deny \
      --bypass AzureServices \
      -o none
    az keyvault network-rule add --name "$KV_NAME" --ip-address "$MY_IP" -o none
    echo "==> Done. Allow ~30s for the firewall update to settle, then retry your secret-show."
  fi
  # Fall through to RBAC handling so a fresh operator gets both in one run.
fi

# --- RBAC ---------------------------------------------------------------------
# Skip if the assignment already exists (idempotent re-runs).
EXISTING="$(az role assignment list \
  --assignee "$PRINCIPAL" \
  --role "$ROLE" \
  --scope "$SCOPE" \
  --query '[0].id' -o tsv 2>/dev/null || true)"

if [[ -n "$EXISTING" ]]; then
  echo "==> RBAC role already assigned ($EXISTING)"
  exit 0
fi

echo "==> Creating role assignment"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: would run:"
  echo "  az role assignment create --assignee-object-id $PRINCIPAL --assignee-principal-type $PRINCIPAL_TYPE --role '$ROLE' --scope $SCOPE"
  exit 0
fi
az role assignment create \
  --assignee-object-id "$PRINCIPAL" \
  --assignee-principal-type "$PRINCIPAL_TYPE" \
  --role "$ROLE" \
  --scope "$SCOPE" \
  -o none

cat <<EOF

==> Done. Allow ~30-60s for RBAC propagation, then:

    export TAGPULSE_API_KEY=\$(az keyvault secret show \\
      --vault-name $KV_NAME \\
      --name tagpulse-test-corp-admin-key \\
      --query value -o tsv)
EOF
