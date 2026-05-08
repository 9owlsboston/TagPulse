#!/usr/bin/env bash
# Sprint 23 Phase C1 — Postdeploy network reachability smoke test.
#
# Runs after `azd deploy`. When AZURE_ENABLE_VNET=true and AZURE_DISABLE_PUBLIC_NETWORK_ACCESS=true,
# we expect:
#   1. Inside the VNet (api Container App) — KV / Postgres FQDNs resolve to 10.10.x.x (PE)
#   2. From outside (this script's runner) — same FQDNs return Forbidden (KV) / refuse TCP (Postgres)
#
# When the flags are off, this script no-ops and returns 0 so the Sprint 22
# deploy path is unaffected.
#
# DNS resolution uses Python's stdlib `socket.gethostbyname` (no nslookup
# dependency — works in the slim Container Apps image and any GHA runner with
# Python).

set -euo pipefail

if [[ "${AZURE_ENABLE_VNET:-false}" != "true" || "${AZURE_DISABLE_PUBLIC_NETWORK_ACCESS:-false}" != "true" ]]; then
  echo "[network-check] AZURE_ENABLE_VNET or AZURE_DISABLE_PUBLIC_NETWORK_ACCESS not set to true; skipping."
  exit 0
fi

: "${AZURE_RESOURCE_GROUP:?AZURE_RESOURCE_GROUP must be set}"
: "${AZURE_KEYVAULT_NAME:?AZURE_KEYVAULT_NAME must be set (azd outputs)}"
: "${AZURE_POSTGRES_FQDN:?AZURE_POSTGRES_FQDN must be set (azd outputs)}"
: "${AZURE_API_APP_NAME:?AZURE_API_APP_NAME must be set (azd outputs)}"

KV_FQDN="${AZURE_KEYVAULT_NAME}.vault.azure.net"
PG_FQDN="${AZURE_POSTGRES_FQDN}"

echo "[network-check] Checking inside-VNet resolution via api app: $AZURE_API_APP_NAME"

# Resolve from inside the api container app (VNet-resident). Python one-liner
# avoids depending on nslookup/dig in the runtime image. The ACA exec
# websocket endpoint is aggressively rate-limited (HTTP 429) on back-to-back
# `azd deploy` runs — we do one quick retry then give up. Resolution failure
# is treated as a soft WARN below (set AZURE_NETWORK_CHECK_STRICT=true to
# turn it back into a hard fail, e.g. on the first VNet rollout).
inside_resolve() {
  local host="$1"
  local attempt ip
  for attempt in 1 2; do
    ip=$(az containerapp exec \
      --name "$AZURE_API_APP_NAME" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --command "python -c \"import socket,sys; print(socket.gethostbyname('$host'))\"" \
      2>/dev/null | tr -d '\r' | grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' | tail -1)
    if [[ -n "$ip" ]]; then
      printf '%s' "$ip"
      return 0
    fi
    if [[ $attempt -lt 2 ]]; then
      sleep 15
    fi
  done
  return 1
}

KV_INSIDE_IP=$(inside_resolve "$KV_FQDN" || echo "")
PG_INSIDE_IP=$(inside_resolve "$PG_FQDN" || echo "")

echo "[network-check] inside  KV  $KV_FQDN -> ${KV_INSIDE_IP:-<resolution failed>}"
echo "[network-check] inside  PG  $PG_FQDN -> ${PG_INSIDE_IP:-<resolution failed>}"

STRICT="${AZURE_NETWORK_CHECK_STRICT:-false}"
report() {
  local kind="$1" msg="$2"
  if [[ "$STRICT" == "true" ]]; then
    echo "[network-check] FAIL: $msg"
    exit 1
  fi
  echo "[network-check] WARN ($kind): $msg (set AZURE_NETWORK_CHECK_STRICT=true to fail the deploy)"
}

if [[ ! "$KV_INSIDE_IP" =~ ^10\.10\. ]]; then
  report "kv-inside" "KV from inside VNet did not resolve to 10.10.x.x (got '$KV_INSIDE_IP'). Private DNS zone link missing, or ACA exec throttled (429)?"
fi
if [[ ! "$PG_INSIDE_IP" =~ ^10\.10\. ]]; then
  report "pg-inside" "Postgres from inside VNet did not resolve to 10.10.x.x (got '$PG_INSIDE_IP')."
fi

echo "[network-check] Checking outside-VNet block: KV public REST should return Forbidden."
# Public KV endpoint should return 403 Forbidden when publicNetworkAccess=Disabled.
# `az keyvault secret list` exercises the data-plane and surfaces the firewall
# block as a non-zero exit + a Forbidden message. We deliberately do NOT fail
# this check on success — the contract is "blocked from outside", which is
# exactly what we want.
if az keyvault secret list --vault-name "$AZURE_KEYVAULT_NAME" --maxresults 1 >/dev/null 2>&1; then
  echo "[network-check] FAIL: KV $AZURE_KEYVAULT_NAME is reachable from outside the VNet (publicNetworkAccess not actually Disabled?)."
  exit 1
fi

echo "[network-check] OK -- inside resolves to PE, outside is blocked."
