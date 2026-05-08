#!/usr/bin/env bash
# scripts/azd-kv-recover.sh [<env>]
#
# Recover any soft-deleted Azure Key Vault whose name matches the active
# environment's name-prefix (`${AZURE_NAME_PREFIX}-kv-*`), so that the next
# `azd provision` does not fail with `VaultAlreadyExists`.
#
# Background: Key Vault names are globally unique AND soft-deleted vaults
# reserve the name for 7–90 days. If you tear down a TagPulse env (azd down
# without --purge) or a single resource fails after the KV has been created,
# rerunning `azd up` collides on the same name. This script restores the
# soft-deleted vault in place so the redeploy succeeds.
#
# Behavior:
#   - Reads AZURE_NAME_PREFIX + AZURE_LOCATION from the active azd env.
#   - Lists soft-deleted vaults in the subscription.
#   - For every match (same prefix + same region), runs `az keyvault recover`.
#   - Skips vaults already active. Skips silently if no matches.
#   - Idempotent: safe to run any time.
#
# Wired as the `preprovision` hook in azure.yaml so `azd up` is self-healing.
# Run manually if you want to recover before invoking azd.
#
# Usage:
#     scripts/azd-kv-recover.sh            # use current azd env
#     scripts/azd-kv-recover.sh dev        # select env first

set -euo pipefail

ENV_NAME="${1:-}"

command -v az >/dev/null 2>&1 || { echo "error: az CLI not on PATH" >&2; exit 1; }
command -v azd >/dev/null 2>&1 || { echo "error: azd not on PATH" >&2; exit 1; }

if [[ -n "$ENV_NAME" ]]; then
  azd env select "tagpulse-${ENV_NAME}" >/dev/null 2>&1 || {
    echo "error: azd env tagpulse-${ENV_NAME} does not exist" >&2; exit 1
  }
fi

# Pull values from azd; tolerate missing keys.
get() {
  # azd env get-value writes "ERROR: key not found ..." to stdout (not stderr)
  # and exits non-zero when the key is missing. Suppress both and emit empty
  # so callers can rely on `-z` / default-value checks.
  local v
  if v=$(azd env get-value "$1" 2>/dev/null); then
    printf '%s' "$v" | tr -d '\r'
  fi
}
NAME_PREFIX=$(get AZURE_NAME_PREFIX)
LOCATION=$(get AZURE_LOCATION)
SUB=$(get AZURE_SUBSCRIPTION_ID)

if [[ -z "$NAME_PREFIX" || -z "$LOCATION" ]]; then
  echo "[kv-recover] AZURE_NAME_PREFIX or AZURE_LOCATION not set in azd env — skipping"
  exit 0
fi

# Make sure az CLI targets the right subscription.
if [[ -n "$SUB" ]]; then
  az account set --subscription "$SUB" >/dev/null 2>&1 || true
fi

echo "[kv-recover] checking for soft-deleted vaults matching '${NAME_PREFIX}-kv-*' (active region: ${LOCATION})"

# Current KV name suffix in use (set by a previous run when a purge-protected
# collision forced us to dodge the reserved name). When set, the active vault
# is `${prefix}-kv-${unique8}-${suffix}` — older soft-deleted vaults named
# `${prefix}-kv-${unique8}` no longer conflict with it, so we leave them alone.
ACTIVE_KV_SUFFIX=$(get AZURE_KV_NAME_SUFFIX)

# Key Vault names are globally unique, so a soft-deleted vault in ANY region
# blocks reuse of the name. Match by prefix across all regions; recover same-
# region collisions in place, and purge other-region collisions (they can't
# be recovered into the new region anyway).
MATCHES_JSON=$(az keyvault list-deleted \
  --query "[?starts_with(name, '${NAME_PREFIX}-kv-')].{name:name, location:properties.location}" \
  -o tsv 2>/dev/null || true)

if [[ -z "$MATCHES_JSON" ]]; then
  echo "[kv-recover] no soft-deleted vaults to recover"
  exit 0
fi

while IFS=$'\t' read -r kv kv_loc; do
  [[ -z "$kv" ]] && continue

  # If we've already bumped the suffix, only act on a vault that matches the
  # CURRENT name (i.e. ends in `-${ACTIVE_KV_SUFFIX}`). Anything else is a
  # stale soft-deleted artifact from before the bump and is no longer a
  # collision.
  if [[ -n "$ACTIVE_KV_SUFFIX" && "$kv" != *-"$ACTIVE_KV_SUFFIX" ]]; then
    echo "[kv-recover] ignoring stale soft-deleted vault (no collision): $kv"
    continue
  fi

  if [[ "$kv_loc" == "$LOCATION" ]]; then
    echo "[kv-recover] recovering in-place: $kv ($kv_loc)"
    if az keyvault recover --name "$kv" --location "$kv_loc" >/dev/null 2>&1; then
      echo "[kv-recover]   ✓ recovered $kv"
      continue
    fi
    echo "[kv-recover]   recover failed; falling back to suffix bump" >&2
  else
    echo "[kv-recover] cross-region collision: $kv (was in $kv_loc, current env uses $LOCATION)"
    if az keyvault purge --name "$kv" --location "$kv_loc" >/dev/null 2>&1; then
      echo "[kv-recover]   ✓ purged $kv"
      continue
    fi
    echo "[kv-recover]   purge failed (likely purge-protection); falling back to suffix bump" >&2
  fi

  # Last resort: dodge the reserved name by appending a short random suffix to
  # the Key Vault name only. Consumed by the `keyVaultNameSuffix` bicep param
  # via AZURE_KV_NAME_SUFFIX.
  NEW_SUFFIX=$(LC_ALL=C head -c 64 /dev/urandom | tr -dc 'a-z0-9' | cut -c1-4)
  echo "[kv-recover]   setting AZURE_KV_NAME_SUFFIX=${NEW_SUFFIX} to avoid '${kv}'"
  azd env set AZURE_KV_NAME_SUFFIX "$NEW_SUFFIX" >/dev/null
  ACTIVE_KV_SUFFIX="$NEW_SUFFIX"
done <<< "$MATCHES_JSON"

echo "[kv-recover] done"
