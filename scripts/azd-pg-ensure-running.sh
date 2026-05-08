#!/usr/bin/env bash
# Ensure the env's Postgres Flexible Server is running.
#
# Burstable-tier Flex servers auto-stop after 7 days of inactivity, and dev
# servers are frequently stopped manually to save cost. When the server is
# Stopped the api keeps running but every DB call fails (asyncpg
# `ConnectionDoesNotExistError` / `TimeoutError`), `/health/ready` flips
# unhealthy, and ACA blocks ingress traffic to the replica — full backend
# outage from the user's perspective.
#
# This script is idempotent: no-op when the server is already Ready, starts it
# (and waits up to ~5 min) when Stopped. Safe to run as a `predeploy` hook or
# manually.
#
# Reads from azd env (preferred) or falls back to plain env vars:
#   AZURE_RESOURCE_GROUP    e.g. tagpulse-dev-rg
#   AZURE_POSTGRES_FQDN     e.g. tpdev-pg-mwig6fst.postgres.database.azure.com
#
# Usage:
#   scripts/azd-pg-ensure-running.sh                 # uses current azd env
#   AZURE_RESOURCE_GROUP=… AZURE_POSTGRES_FQDN=… scripts/azd-pg-ensure-running.sh
#
# Exit codes:
#   0  server is Ready (or was started successfully)
#   1  blocking error (auth, missing inputs, start failed)
#   2  start kicked off but server didn't reach Ready within timeout
set -u

# Pull from azd env when not already exported.
get_azd() {
  if command -v azd >/dev/null 2>&1; then
    azd env get-value "$1" 2>/dev/null | tr -d '\r'
  fi
}

RG="${AZURE_RESOURCE_GROUP:-$(get_azd AZURE_RESOURCE_GROUP)}"
PG_FQDN="${AZURE_POSTGRES_FQDN:-$(get_azd AZURE_POSTGRES_FQDN)}"

if [[ -z "${RG:-}" || -z "${PG_FQDN:-}" ]]; then
  echo "[pg-ensure] AZURE_RESOURCE_GROUP and AZURE_POSTGRES_FQDN must be set (or available via azd env)." >&2
  exit 1
fi

# server name = first label of the FQDN (everything before the first dot).
PG_NAME="${PG_FQDN%%.*}"
if [[ -z "$PG_NAME" ]]; then
  echo "[pg-ensure] could not derive server name from FQDN '$PG_FQDN'" >&2
  exit 1
fi

STATE=$(az postgres flexible-server show -n "$PG_NAME" -g "$RG" --query state -o tsv 2>/dev/null || true)
if [[ -z "$STATE" ]]; then
  echo "[pg-ensure] could not read server state — check 'az login' and that '$PG_NAME' exists in '$RG'." >&2
  exit 1
fi

echo "[pg-ensure] $PG_NAME state=$STATE"

case "$STATE" in
  Ready)
    exit 0
    ;;
  Starting)
    : # fall through to poll loop
    ;;
  Stopped|Disabled)
    echo "[pg-ensure] starting $PG_NAME ..."
    if ! az postgres flexible-server start -n "$PG_NAME" -g "$RG" --no-wait -o none; then
      echo "[pg-ensure] start command failed" >&2
      exit 1
    fi
    ;;
  *)
    echo "[pg-ensure] unexpected state '$STATE' — refusing to act" >&2
    exit 1
    ;;
esac

# Poll until Ready (or give up after ~5 min). Burstable cold start ~60–120s.
for i in $(seq 1 20); do
  sleep 15
  STATE=$(az postgres flexible-server show -n "$PG_NAME" -g "$RG" --query state -o tsv 2>/dev/null || echo "?")
  echo "[pg-ensure] $(date -u +%H:%M:%S) state=$STATE"
  if [[ "$STATE" == "Ready" ]]; then
    exit 0
  fi
done

echo "[pg-ensure] timed out waiting for $PG_NAME to reach Ready (last state: $STATE)" >&2
exit 2
