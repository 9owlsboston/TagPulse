#!/usr/bin/env bash
# scripts/azd-kv-get.sh <env> <secret-name>
# scripts/azd-kv-get.sh <env> --list
# scripts/azd-kv-get.sh <env> --tenant <tenant-slug>
# scripts/azd-kv-get.sh <env> --names <name1,name2,...>
#
# Retrieve a Key Vault secret value (or list secret names) via the in-VNet
# tools-job, bypassing the KV firewall entirely. Designed for operators on
# locked-down laptops where `az keyvault secret show` from the laptop hits
# ForbiddenByFirewall (Sprint 23-B sets publicNetworkAccess=Disabled, and
# rotating corporate SNAT IPs make ipRules-allowlisting impractical — see
# scripts/azd-grant-operator-kv.sh for the IP-allowlist alternative).
#
# Each invocation cold-starts the in-VNet tools-job (~1-2 min). The --tenant
# and --names batch modes fetch several secrets in ONE job run so you pay that
# cost once instead of once per secret. Prefer them over a shell `for` loop of
# single-secret calls.
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
#      `python scripts/get_kv_secret.py --name <secret-name>` (or --names/--list).
#   2. Starts a job execution; the container runs in the VNet with the
#      workload UAMI, which already has 'Key Vault Secrets User' on the KV.
#   3. Streams the container's stdout live via the Container Apps data-plane
#      log endpoint (no Log Analytics ingestion lag).
#   4. Extracts the value/names between sentinels and prints them on stdout,
#      so the script is suitable for
#      `export FOO=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)`.
#
# Examples:
#   scripts/azd-kv-get.sh dev --list                                         # menu
#   export TAGPULSE_API_KEY=$(scripts/azd-kv-get.sh dev tagpulse-test-corp-admin-key)
#   scripts/azd-kv-get.sh dev mqtt-broker-password
#   scripts/azd-kv-get.sh dev --tenant demo-wm-dc      # admin+editor+viewer keys, one job
#   scripts/azd-kv-get.sh dev --names jwt-secret,mqtt-broker-password
#
# Note: --tenant / --names print a labeled table of multiple values — do NOT
# wrap them in $(...); use single-secret mode for command substitution.

set -euo pipefail

if [[ $# -lt 2 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,47p' "$0"
  exit 1
fi

ENV_NAME="$1"
TARGET="$2"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$(mktemp)"
trap 'rm -f "$LOG_FILE"' EXIT

# Branch on --list / --tenant / --names (batch) vs single-secret retrieval.
# All four modes share one in-VNet job execution, so the batch modes pay the
# tools-job cold-start cost (~1-2 min) exactly once instead of once per secret.
MODE="single"
if [[ "$TARGET" == "--list" ]]; then
  JOB_ARGS=(--list)
  BEGIN_SENTINEL='===KV_SECRET_LIST_BEGIN==='
  END_SENTINEL='===KV_SECRET_LIST_END==='
  MODE="list"
elif [[ "$TARGET" == "--tenant" ]]; then
  # Convenience: fetch admin+editor+viewer keys for a demo/test tenant in one
  # job run. `scripts/azd-kv-get.sh dev --tenant demo-wm-dc`
  TENANT_SLUG="${3:-}"
  if [[ -z "$TENANT_SLUG" ]]; then
    echo "error: --tenant requires a slug, e.g. --tenant demo-wm-dc" >&2
    exit 1
  fi
  NAMES="tagpulse-${TENANT_SLUG}-admin-key,tagpulse-${TENANT_SLUG}-editor-key,tagpulse-${TENANT_SLUG}-viewer-key"
  JOB_ARGS=(--names "$NAMES")
  BEGIN_SENTINEL='===KV_SECRET_MULTI_BEGIN==='
  END_SENTINEL='===KV_SECRET_MULTI_END==='
  MODE="multi"
elif [[ "$TARGET" == "--names" ]]; then
  # Explicit batch: `scripts/azd-kv-get.sh dev --names jwt-secret,mqtt-broker-password`
  NAMES="${3:-}"
  if [[ -z "$NAMES" ]]; then
    echo "error: --names requires a comma-separated list of secret names" >&2
    exit 1
  fi
  JOB_ARGS=(--names "$NAMES")
  BEGIN_SENTINEL='===KV_SECRET_MULTI_BEGIN==='
  END_SENTINEL='===KV_SECRET_MULTI_END==='
  MODE="multi"
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
# because the streamed log can wrap or interleave timestamps. The wrapper
# (azd-job.sh) prefixes every log line with 4 spaces; strip it before we
# return the value to stdout so callers get the bare secret.
PAYLOAD="$(awk -v b="$BEGIN_SENTINEL" -v e="$END_SENTINEL" \
  '$0 ~ b {flag=1; next} $0 ~ e {flag=0} flag' "$LOG_FILE" \
  | sed -E 's/^ {4}//')"

if [[ -z "$PAYLOAD" ]]; then
  echo "error: could not extract output from job log; full log below:" >&2
  cat "$LOG_FILE" >&2
  exit 1
fi

if [[ "$MODE" == "list" ]]; then
  # Print the full list (one name per line).
  printf '%s\n' "$PAYLOAD"
elif [[ "$MODE" == "multi" ]]; then
  # Batch: payload is 'name=value' lines. Print as an aligned, labeled table
  # so the keys are easy to read/copy. This mode is meant for interactive use
  # (don't wrap it in $(...) — there are multiple values).
  printf '%s\n' "$PAYLOAD" | awk -F= '
    { name=$1; sub(/^[^=]*=/, "", $0); value=$0; rows[NR]=name; vals[NR]=value;
      if (length(name) > w) w = length(name) }
    END {
      for (i = 1; i <= NR; i++) printf "%-*s  %s\n", w, rows[i], vals[i]
    }'
else
  # Single secret value — print the entire sentinel block. Multi-line secrets
  # (e.g. PEM-encoded CA bundles like `mqtt-tls-ca`) must be preserved verbatim;
  # the awk extraction above is already sentinel-bounded so no further trimming
  # is needed.
  printf '%s\n' "$PAYLOAD"
fi
