#!/usr/bin/env bash
# scripts/azd-logs.sh <env> <service> [--since <duration>] [--follow]
#
# Tail Container App / Job logs for a deployed env. Wraps
# `az containerapp logs show` with the right resource name resolved via
# scripts/lib/azd-common.sh.
#
# service ∈ api | worker | mqtt | migrations | tools

set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"

ENV_NAME="${1:-}"
SERVICE="${2:-}"
shift 2 || true
SINCE=""
FOLLOW=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    --follow|-f) FOLLOW=1; shift ;;
    *) die "Unknown flag: $1" 2 ;;
  esac
done

[[ -z "$ENV_NAME" || -z "$SERVICE" ]] && die "Usage: $0 <env> <api|worker|mqtt|migrations|tools> [--since 15m] [--follow]" 2

azd_env_resolve "$ENV_NAME"
NAME="$(aca_name "$ENV_SHORT" "$SERVICE")"
[[ -z "$NAME" ]] && die "Could not resolve a $SERVICE resource in $RG" 2

case "$SERVICE" in
  mqtt)
    # Mosquitto runs on ACI, not ACA.
    log "tailing ACI logs for $NAME (rg=$RG)"
    az container logs --name "$NAME" --resource-group "$RG" $([[ "$FOLLOW" == "1" ]] && echo "--follow")
    ;;
  migrations|tools)
    log "tailing latest job execution logs for $NAME (rg=$RG)"
    EXEC=$(az containerapp job execution list --name "$NAME" --resource-group "$RG" \
      --query 'sort_by([], &properties.startTime)[-1].name' -o tsv 2>/dev/null || echo '')
    [[ -z "$EXEC" ]] && die "No executions found for job $NAME" 1
    az containerapp job logs show --name "$NAME" --resource-group "$RG" --execution "$EXEC" \
      $([[ "$FOLLOW" == "1" ]] && echo "--follow")
    ;;
  api|worker)
    log "tailing Container App logs for $NAME (rg=$RG)"
    az containerapp logs show --name "$NAME" --resource-group "$RG" \
      $([[ -n "$SINCE" ]] && echo "--tail 1000") \
      $([[ "$FOLLOW" == "1" ]] && echo "--follow")
    ;;
esac
