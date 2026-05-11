#!/usr/bin/env bash
# scripts/azd-smoke.sh <env>
#
# Sprint 28 A5: post-deploy smoke. Curl /healthz, /readyz, and /tenant/config
# against a deployed env. Exit non-zero on first failure with the response
# in stderr. Designed to be wired into CI after `azd deploy` succeeds.
#
# Reads the test-corp tenant key from KV via azd-common's kv_secret_get if
# the secret exists; falls back to skipping the authenticated /tenant/config
# probe with a warning when KV access isn't granted to the caller.

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"

ENV_NAME="${1:-}"
[[ -z "$ENV_NAME" ]] && die "Usage: $0 <env>" 2

azd_env_resolve "$ENV_NAME"
log "smoke against env $RESOLVED_AZD_ENV (rg=$RG)"

API_FQDN="$(az containerapp show \
  --name "$(aca_name "$ENV_SHORT" api)" \
  --resource-group "$RG" \
  --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || echo '')"
[[ -z "$API_FQDN" ]] && die "Could not resolve api FQDN — is the api Container App deployed?" 2
API_URL="https://$API_FQDN"
log "api: $API_URL"

FAILED=0
check() {
  local label="$1"; local url="$2"; local expect="${3:-200}"
  local extra=("${@:4}")
  local code body tmp
  tmp=$(mktemp)
  code=$(curl -sS -o "$tmp" -w '%{http_code}' --max-time 10 "${extra[@]}" "$url" || echo '000')
  body=$(head -c 200 "$tmp" 2>/dev/null || echo '')
  rm -f "$tmp"
  if [[ "$code" == "$expect" ]]; then
    printf '  ✓ %-32s %s\n' "$label" "$code" >&2
  else
    printf '  ✗ %-32s %s (expected %s)\n    %s\n' "$label" "$code" "$expect" "$body" >&2
    FAILED=$((FAILED + 1))
  fi
}

check "GET /healthz"  "$API_URL/healthz"
check "GET /readyz"   "$API_URL/readyz"

# Authenticated probe — needs the test-corp admin key.
KEY="$(az keyvault secret show --vault-name "$KV_NAME" --name "tagpulse-test-corp-admin-key" --query value -o tsv 2>/dev/null || echo '')"
if [[ -n "$KEY" ]]; then
  check "GET /tenant/config (auth)" "$API_URL/tenant/config" 200 -H "Authorization: Bearer $KEY"
else
  log "warning: tagpulse-test-corp-admin-key not readable; skipping authenticated probe"
fi

if [[ "$FAILED" -gt 0 ]]; then
  die "$FAILED smoke check(s) failed"
fi
log "all smoke checks passed"
