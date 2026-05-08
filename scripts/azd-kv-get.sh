#!/usr/bin/env bash
# scripts/azd-kv-get.sh <env> <secret-name>
#
# Retrieve a Key Vault secret value via the in-VNet tools-job, bypassing the
# KV firewall entirely. Designed for operators on locked-down laptops where
# `az keyvault secret show` from the laptop hits ForbiddenByFirewall (Sprint
# 23-B sets publicNetworkAccess=Disabled, and rotating corporate SNAT IPs
# make ipRules-allowlisting impractical — see scripts/azd-grant-operator-kv.sh
# for the IP-allowlist alternative).
#
# How it works:
#   1. Updates the tagpulse-<env>-tools job's command to run
#      `python scripts/get_kv_secret.py --name <secret-name>`.
#   2. Starts a job execution; the container runs in the VNet with the
#      workload UAMI, which already has 'Key Vault Secrets User' on the KV.
#   3. Tails Log Analytics until the run finishes.
#   4. Extracts the value between ===KV_SECRET_BEGIN===/===KV_SECRET_END===
#      sentinels and prints it on stdout, so this script is suitable for
#      `export FOO=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)`.
#
# Cost: one job execution (~30-60s of cold-start + Python + KV roundtrip,
# plus log query). No KV firewall changes; no operator RBAC required beyond
# what scripts/azd-job.sh already needs (Container Apps Job Operator on the
# job, which the platform team grants alongside Reader on the RG).
#
# Examples:
#   export TAGPULSE_API_KEY=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)
#   scripts/azd-kv-get.sh dev mqtt-broker-password

set -euo pipefail

if [[ $# -lt 2 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,30p' "$0"
  exit 1
fi

ENV_NAME="$1"
SECRET_NAME="$2"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT

# Run the in-VNet job. --allow-stale because we don't ship the wrapper itself
# in the image; the script (get_kv_secret.py) is what matters and it's already
# in the deployed image as long as the image was built since this script
# landed. Operators who haven't redeployed since then get a clear error from
# the job ("python: can't open file 'scripts/get_kv_secret.py'") and need to
# `azd deploy` first.
"$REPO_ROOT/scripts/azd-job.sh" "$ENV_NAME" get_kv_secret.py \
  --allow-stale -- --name "$SECRET_NAME" >"$LOG_FILE" 2>&1 || {
    echo "error: tools-job execution failed; full log below:" >&2
    cat "$LOG_FILE" >&2
    exit 1
  }

# Extract the value between sentinels. awk is more robust than sed here
# because the streamed log can wrap or interleave timestamps.
SECRET_VALUE="$(awk '/===KV_SECRET_BEGIN===/{flag=1; next} /===KV_SECRET_END===/{flag=0} flag' "$LOG_FILE" | tail -1)"

if [[ -z "$SECRET_VALUE" ]]; then
  echo "error: could not extract secret value from job log; full log below:" >&2
  cat "$LOG_FILE" >&2
  exit 1
fi

printf '%s\n' "$SECRET_VALUE"
