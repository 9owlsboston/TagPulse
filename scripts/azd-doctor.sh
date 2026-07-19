#!/usr/bin/env bash
# scripts/azd-doctor.sh <env>
#
# Sprint 28 F3: aggregate health check for an env. Runs a small battery of
# read-only checks and prints a green/yellow/red dashboard. Exit code = number
# of red checks.
#
# Checks (all best-effort; missing dependency = yellow, not red):
#   1. azd env resolves
#   2. RG exists and is in expected state
#   3. api Container App is running and reachable on /health/ready
#   4. worker Container App has 1+ replicas
#   5. Mosquitto ACI is in 'Running' state
#   6. PG Flexible Server is 'Ready'
#   7. KV is reachable from this principal (list secrets)
#   8. ACR has the latest image tag from main
#   9. No Azure Monitor alerts firing for the RG
#  10. KV secrets expiring within 30 days

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"

ENV_NAME="${1:-}"
[[ -z "$ENV_NAME" ]] && die "Usage: $0 <env>" 2

GREEN=0; YELLOW=0; RED=0

emit() {
  local status="$1"; shift
  local symbol
  case "$status" in
    green)  symbol="✓"; GREEN=$((GREEN+1)) ;;
    yellow) symbol="!"; YELLOW=$((YELLOW+1)) ;;
    red)    symbol="✗"; RED=$((RED+1)) ;;
  esac
  printf '  %s %-40s %s\n' "$symbol" "$1" "${2:-}"
}

echo "==> azd-doctor: env=$ENV_NAME"

# 1. azd env resolves
# Don't swallow stderr here: azd_env_resolve calls die() on failure, which
# exits the whole script (exit, not return), so the `else` branch below is
# unreachable. Let the underlying error message reach the operator instead
# of leaving them with a bare `Error 2` from make.
if azd_env_resolve "$ENV_NAME"; then
  emit green "azd env resolves" "$RESOLVED_AZD_ENV"
else
  emit red "azd env resolves" "(env '$ENV_NAME' not found)"
  exit 1
fi

# 2. RG exists
if az group show --name "$RG" >/dev/null 2>&1; then
  emit green "resource group" "$RG"
else
  emit red "resource group" "$RG missing"
fi

# 3. api running + readiness (/health/ready)
#    The app exposes /health (FastAPI), /health/ready, /health/live.
#    /healthz is NOT a route — earlier versions of this script probed it
#    and reported a false red. Probe /health/ready (the readiness path
#    documented in azure-first-deploy.md).
api_name=$(aca_name "$ENV_SHORT" api)
if [[ -n "$api_name" ]]; then
  fqdn=$(az containerapp show -n "$api_name" -g "$RG" --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || echo '')
  if [[ -n "$fqdn" ]]; then
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "https://$fqdn/health/ready" || echo 000)
    if [[ "$code" == "200" ]]; then
      emit green "api /health/ready" "$fqdn"
    else
      emit red "api /health/ready" "HTTP $code"
    fi
  else
    emit yellow "api ingress" "fqdn not resolvable"
  fi
else
  emit red "api Container App" "not found"
fi

# 4. worker replicas
worker_name=$(aca_name "$ENV_SHORT" worker)
if [[ -n "$worker_name" ]]; then
  replicas=$(az containerapp replica list -n "$worker_name" -g "$RG" --query 'length(@)' -o tsv 2>/dev/null || echo 0)
  if [[ "${replicas:-0}" -ge 1 ]]; then
    emit green "worker replicas" "$replicas"
  else
    emit red "worker replicas" "0 replicas"
  fi
else
  emit yellow "worker Container App" "not found"
fi

# 5. Mosquitto (Container App OR ACI depending on env)
mqtt_name=$(aca_name "$ENV_SHORT" mqtt)
if [[ -n "$mqtt_name" ]]; then
  # Try ACI first (current dev shape), fall back to ACA.
  state=$(az container show -n "$mqtt_name" -g "$RG" --query 'instanceView.state' -o tsv 2>/dev/null || echo '')
  if [[ -z "$state" ]]; then
    state=$(az containerapp show -n "$mqtt_name" -g "$RG" --query 'properties.runningStatus' -o tsv 2>/dev/null || echo Unknown)
  fi
  if [[ "$state" == "Running" ]]; then
    emit green "mosquitto" "$mqtt_name = $state"
  else
    emit red "mosquitto" "$mqtt_name = $state"
  fi
else
  emit yellow "mosquitto" "not found (in-VNet ACA env? check naming)"
fi

# 6. PG Flexible Server
pg_name=$(az postgres flexible-server list -g "$RG" --query '[0].name' -o tsv 2>/dev/null || echo '')
if [[ -n "$pg_name" ]]; then
  state=$(az postgres flexible-server show -n "$pg_name" -g "$RG" --query 'state' -o tsv 2>/dev/null || echo Unknown)
  if [[ "$state" == "Ready" ]]; then
    emit green "postgres state" "$pg_name = $state"
  else
    emit red "postgres state" "$pg_name = $state"
  fi
else
  emit red "postgres flexible server" "none in RG"
fi

# 7. KV reachable
if [[ -n "${KV_NAME:-}" ]] && az keyvault secret list --vault-name "$KV_NAME" --maxresults 1 >/dev/null 2>&1; then
  emit green "key vault reachable" "$KV_NAME"
elif [[ -n "${KV_NAME:-}" ]]; then
  emit yellow "key vault reachable" "$KV_NAME (run: scripts/azd-grant-operator-kv.sh $ENV_SHORT --allow-my-ip)"
else
  emit yellow "key vault" "name not set in azd env"
fi

# 8. ACR latest image
if [[ -n "${ACR_NAME:-}" ]]; then
  latest_tag=$(az acr repository show-tags --name "$ACR_NAME" --repository tagpulse-api \
    --orderby time_desc --top 1 -o tsv 2>/dev/null || echo '')
  if [[ -n "$latest_tag" ]]; then
    emit green "ACR tagpulse-api latest" "$latest_tag"
  else
    emit yellow "ACR tagpulse-api" "no tags found"
  fi
else
  emit yellow "ACR name" "not set in azd env"
fi

# 9. Active alerts
active=$(az monitor activity-log alert list -g "$RG" --query 'length(@)' -o tsv 2>/dev/null || echo '?')
if [[ "$active" == "0" ]]; then
  emit green "active activity-log alerts" "0"
elif [[ "$active" == "?" ]]; then
  emit yellow "active activity-log alerts" "(query failed)"
else
  emit yellow "active activity-log alerts" "$active configured (status not checked)"
fi

# 10. KV secrets expiring within 30 days
if [[ -n "${KV_NAME:-}" ]]; then
  threshold=$(date -u -d '+30 days' +%s 2>/dev/null || python3 -c 'import time; print(int(time.time()) + 30*86400)')
  expiring=$(az keyvault secret list --vault-name "$KV_NAME" \
    --query "[?attributes.expires!=null && attributes.enabled].{n:name,e:attributes.expires}" \
    -o json 2>/dev/null \
    | python3 -c "
import json, sys, datetime
threshold = $threshold
items = json.load(sys.stdin)
soon = []
for it in items:
    try:
        ts = datetime.datetime.fromisoformat(it['e'].replace('Z','+00:00')).timestamp()
    except Exception:
        continue
    if ts <= threshold:
        soon.append(it['n'])
print(','.join(soon))
" 2>/dev/null || echo '')
  if [[ -z "$expiring" ]]; then
    emit green "KV secrets expiring (30d)" "none"
  else
    emit yellow "KV secrets expiring (30d)" "$expiring"
  fi
fi

echo
echo "==> Summary: $GREEN green, $YELLOW yellow, $RED red"
exit "$RED"
