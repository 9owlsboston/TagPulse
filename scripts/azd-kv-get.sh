#!/usr/bin/env bash
# scripts/azd-kv-get.sh <env> <secret-name>
# scripts/azd-kv-get.sh <env> --list
#
# Retrieve a Key Vault secret value (or list secret names) via the in-VNet
# tools-job, bypassing the KV firewall entirely. Designed for operators on
# locked-down laptops where `az keyvault secret show` from the laptop hits
# ForbiddenByFirewall (Sprint 23-B sets publicNetworkAccess=Disabled, and
# rotating corporate SNAT IPs make ipRules-allowlisting impractical — see
# scripts/azd-grant-operator-kv.sh for the IP-allowlist alternative).
#
# Common secret names (Sprint 22-C deployment):
#   jwt-secret                          # API JWT signing key (Bicep-seeded)
#   postgres-admin-password             # Postgres admin password (Bicep-seeded)
#   mqtt-broker-password                # Mosquitto auth password (Bicep-seeded)
#   tagpulse-<tenant-slug>-admin-key    # smoke_setup --regenerate-key
#   tagpulse-<tenant-slug>-editor-key   # smoke_setup --with-roles --regenerate-key
#   tagpulse-<tenant-slug>-viewer-key   # smoke_setup --with-roles --regenerate-key
# Use `--list` to discover names without guessing.
#
# How it works:
#   1. Updates the tagpulse-<env>-tools job's command to run
#      `python scripts/get_kv_secret.py --name <secret-name>` (or --list).
#   2. Starts a job execution; the container runs in the VNet with the
#      workload UAMI, which already has 'Key Vault Secrets User' on the KV.
#   3. Tails Log Analytics until the run finishes.
#   4. Extracts the value/names between sentinels and prints them on stdout,
#      so the script is suitable for
#      `export FOO=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)`.
#
# Examples:
#   scripts/azd-kv-get.sh dev --list                                         # menu
#   export TAGPULSE_API_KEY=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)
#   scripts/azd-kv-get.sh dev mqtt-broker-password

set -euo pipefail

if [[ $# -lt 2 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,35p' "$0"
  exit 1
fi

ENV_NAME="$1"
TARGET="$2"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT

# Branch on --list vs single-secret retrieval.
if [[ "$TARGET" == "--list" ]]; then
  JOB_ARGS=(--list)
  BEGIN_SENTINEL='===KV_SECRET_LIST_BEGIN==='
  END_SENTINEL='===KV_SECRET_LIST_END==='
else
  JOB_ARGS=(--name "$TARGET")
  BEGIN_SENTINEL='===KV_SECRET_BEGIN==='
  END_SENTINEL='===KV_SECRET_END==='
fi

# Run the in-VNet job. --allow-stale because we don't ship the wrapper itself
# in the image; the script (get_kv_secret.py) is what matters and it's already
# in the deployed image as long as the image was built since this script
# landed. Operators who haven't redeployed since then get a clear error from
# the job ("python: can't open file 'scripts/get_kv_secret.py'") and need to
# `azd deploy` first.
"$REPO_ROOT/scripts/azd-job.sh" "$ENV_NAME" get_kv_secret.py \
  --allow-stale -- "${JOB_ARGS[@]}" >"$LOG_FILE" 2>&1 || {
    echo "error: tools-job execution failed; full log below:" >&2
    cat "$LOG_FILE" >&2
    exit 1
  }

# Extract the payload between sentinels. awk is more robust than sed here
# because the streamed log can wrap or interleave timestamps.
PAYLOAD="$(awk -v b="$BEGIN_SENTINEL" -v e="$END_SENTINEL" \
  '$0 ~ b {flag=1; next} $0 ~ e {flag=0} flag' "$LOG_FILE")"

if [[ -z "$PAYLOAD" ]]; then
  echo "error: could not extract output from job log; full log below:" >&2
  cat "$LOG_FILE" >&2
  exit 1
fi

if [[ "$TARGET" == "--list" ]]; then
  # Print the full list (one name per line).
  printf '%s\n' "$PAYLOAD"
else
  # Single secret value — last line of the sentinel block (defensive against
  # any wrapped/duplicated lines in the log tail).
  printf '%s\n' "$(echo "$PAYLOAD" | tail -1)"
fi
