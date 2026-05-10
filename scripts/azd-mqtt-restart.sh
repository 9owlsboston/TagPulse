#!/usr/bin/env bash
# scripts/azd-mqtt-restart.sh <env>
#
# Sprint 28 C5 — restart the Mosquitto ACI in <env>.
#
# Why a script?
#   ACI doesn't have a "restart container" first-class command — you
#   stop + start the container *group*. Both calls are async and fail
#   silently if the group is already in the requested state. This
#   wrapper:
#     1) resolves the ACI name from the env's RG via aca_name (mqtt kind),
#     2) runs `az container stop` + waits for state=Terminated,
#     3) runs `az container start` + waits for state=Running,
#     4) tails the new container's stderr for ~10s so the operator can
#        see auth failures or cert-load errors immediately,
#     5) prints next-steps (run mqtt_canary, run make doctor).
#
# Usage:
#   scripts/azd-mqtt-restart.sh dev
#   scripts/azd-mqtt-restart.sh production --skip-tail
#
# Exit codes:
#   0 — restart successful, container Running.
#   1 — restart failed (state did not reach Running, or az error).
#   2 — operator setup wrong (env unresolved, ACI not found, etc.).

set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"

ENV_NAME="${1:-}"
SKIP_TAIL=0
shift || true
for arg in "$@"; do
  case "$arg" in
    --skip-tail) SKIP_TAIL=1 ;;
    *) die "unknown arg: $arg" 2 ;;
  esac
done

[[ -z "$ENV_NAME" ]] && die "Usage: $0 <env> [--skip-tail]" 2

azd_env_resolve "$ENV_NAME"
ACI_NAME="$(aca_name "$ENV_SHORT" mqtt || true)"
[[ -z "$ACI_NAME" ]] && die "could not resolve mqtt ACI in rg=$RG" 2

log "restarting Mosquitto ACI: $ACI_NAME (rg=$RG)"

log "stopping..."
az container stop -g "$RG" -n "$ACI_NAME" --output none || die "az container stop failed" 1

# Wait for Terminated
for _ in $(seq 1 30); do
  STATE="$(az container show -g "$RG" -n "$ACI_NAME" \
    --query 'instanceView.state' -o tsv 2>/dev/null || echo '')"
  log "  state=$STATE"
  [[ "$STATE" == "Stopped" || "$STATE" == "Terminated" || "$STATE" == "Succeeded" ]] && break
  sleep 2
done

log "starting..."
az container start -g "$RG" -n "$ACI_NAME" --output none || die "az container start failed" 1

# Wait for Running
RUNNING=0
for _ in $(seq 1 60); do
  STATE="$(az container show -g "$RG" -n "$ACI_NAME" \
    --query 'instanceView.state' -o tsv 2>/dev/null || echo '')"
  log "  state=$STATE"
  if [[ "$STATE" == "Running" ]]; then
    RUNNING=1
    break
  fi
  sleep 2
done

if [[ "$RUNNING" != 1 ]]; then
  die "container did not reach Running state" 1
fi

if [[ "$SKIP_TAIL" != 1 ]]; then
  log "tailing logs for ~10s (Ctrl-C to detach early)..."
  timeout 10 az container logs -g "$RG" -n "$ACI_NAME" --follow 2>&1 | sed 's/^/  /' || true
fi

cat <<EOF

==> Mosquitto restarted successfully.

Next steps:
  1) Verify ingestion end-to-end:
       scripts/azd-job.sh $ENV_SHORT mqtt_canary.py
  2) Verify subscriber re-attached:
       make doctor ENV=$ENV_SHORT
  3) If alerts fired during restart, ack them:
       see docs/runbooks/mqtt-outage.md (Sprint 28 C4)
EOF
