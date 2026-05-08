#!/usr/bin/env bash
# scripts/azd-job.sh <env> <script> [-- <script args…>] [--update-only] [--allow-stale]
#
# Run any scripts/<name>.py inside the deployed tools-job (Sprint 26 B1) and
# stream its stdout back to the operator's terminal. The job runs in-VNet
# with the workload's UAMI, so it can talk to the private Postgres + KV that
# are otherwise unreachable from a laptop.
#
# Examples:
#   scripts/azd-job.sh dev smoke_setup.py -- --full --with-roles --regenerate-key
#   scripts/azd-job.sh dev simulate_devices.py -- --tenant-id 11111111-… --duration 60
#   scripts/azd-job.sh dev smoke_setup.py --update-only      # re-tail last run, no restart
#
# Flags:
#   --update-only   Skip the job-update + start; just re-tail logs from the
#                   most recent execution (recovers from terminal disconnect).
#   --allow-stale   Allow running with a dirty working tree or unpushed
#                   commits. By default we refuse, because the job runs the
#                   *deployed* image — local script edits won't take effect
#                   until the next `azd deploy`.
#
# Exit codes:
#   0   job execution Succeeded
#   1   usage / preflight (missing tool, dirty tree without --allow-stale)
#   2   azd env / Azure auth failure
#   3   job did not reach Succeeded within 30 minutes
#   4   job execution finished with non-Succeeded status (Failed/Stopped/etc)

set -euo pipefail

usage() {
  sed -n '2,30p' "$0"
  exit 1
}

if [[ $# -lt 2 ]]; then
  usage
fi

ENV_NAME="$1"; shift
SCRIPT_NAME="$1"; shift

UPDATE_ONLY=0
ALLOW_STALE=0
SCRIPT_ARGS=()

# Everything between -- and end of line is forwarded to the script unchanged.
# Anything before -- is treated as a flag for this wrapper.
saw_dashdash=0
for arg in "$@"; do
  if [[ "$saw_dashdash" -eq 1 ]]; then
    SCRIPT_ARGS+=("$arg")
    continue
  fi
  case "$arg" in
    --) saw_dashdash=1 ;;
    --update-only) UPDATE_ONLY=1 ;;
    --allow-stale) ALLOW_STALE=1 ;;
    -h|--help) usage ;;
    *)
      echo "error: unknown wrapper flag: $arg (did you forget the -- separator?)" >&2
      exit 1
      ;;
  esac
done

# Preflight: required CLIs.
for cmd in az azd jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: $cmd not on PATH" >&2
    exit 1
  fi
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# The job runs the deployed image. Refuse to use stale local edits unless
# operator explicitly opts in.
if [[ "$ALLOW_STALE" -eq 0 && "$UPDATE_ONLY" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "error: working tree has uncommitted changes." >&2
    echo "       The tools-job runs the *deployed* image, so local edits to" >&2
    echo "       scripts/$SCRIPT_NAME won't take effect until the next" >&2
    echo "       'azd deploy'. Commit + push + redeploy, or pass --allow-stale" >&2
    echo "       if you intentionally want to run the deployed version." >&2
    exit 1
  fi
  upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo '')
  if [[ -n "$upstream" ]]; then
    ahead=$(git rev-list --count "$upstream..HEAD" 2>/dev/null || echo 0)
    if [[ "$ahead" -gt 0 ]]; then
      echo "error: $ahead local commit(s) not yet pushed to $upstream." >&2
      echo "       Push + redeploy first, or pass --allow-stale." >&2
      exit 1
    fi
  fi
fi

# Resolve job name + RG from azd env. The tools-job's name is exposed as
# the `toolsJobName` output of the workload deployment (Sprint 26 B2).
echo "==> Resolving env $ENV_NAME"
if ! azd env select "$ENV_NAME" >/dev/null 2>&1; then
  echo "error: azd env '$ENV_NAME' not found. Run 'azd env list'." >&2
  exit 2
fi
JOB_NAME="$(azd env get-value toolsJobName 2>/dev/null || echo '')"
RG="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo '')"
if [[ -z "$JOB_NAME" || -z "$RG" ]]; then
  echo "error: azd env $ENV_NAME is missing toolsJobName / AZURE_RESOURCE_GROUP." >&2
  echo "       Has 'azd up' been run against this env since Sprint 26 B2 landed?" >&2
  exit 2
fi
LOG_WORKSPACE_ID="$(az containerapp env show \
  --name "$(azd env get-value containerAppsEnvName)" \
  --resource-group "$RG" \
  --query 'properties.appLogsConfiguration.logAnalyticsConfiguration.customerId' \
  -o tsv 2>/dev/null || echo '')"
if [[ -z "$LOG_WORKSPACE_ID" ]]; then
  echo "error: could not resolve Log Analytics workspace id for the env." >&2
  exit 2
fi
echo "    job:        $JOB_NAME"
echo "    rg:         $RG"
echo "    workspace:  $LOG_WORKSPACE_ID"

# Build the args[] array Azure expects: comma-separated string of values.
# `--args 'a,b,c'` arrives in the container as ["a","b","c"]. We always
# invoke `python scripts/<name>.py [args…]` so the operator never has to
# think about /app/scripts/.
JOIN_ARGS=$(printf "scripts/%s" "$SCRIPT_NAME")
for a in "${SCRIPT_ARGS[@]}"; do
  JOIN_ARGS+=",${a}"
done

EXEC_NAME=""
if [[ "$UPDATE_ONLY" -eq 0 ]]; then
  echo "==> Updating job command"
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --image "$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query 'properties.template.containers[0].image' -o tsv)" \
    --set-env-vars "TAGPULSE_JOB_INVOCATION=$(date -u +%Y%m%dT%H%M%SZ)" \
    >/dev/null
  # Container-level command/args live on properties.template.containers[0],
  # not on the top-level configuration. `az containerapp job update` doesn't
  # have first-class flags for them yet (preview gap), so patch via JSON.
  PATCH=$(mktemp)
  trap 'rm -f "$PATCH"' EXIT
  cat >"$PATCH" <<EOF
{
  "properties": {
    "template": {
      "containers": [
        {
          "name": "tools",
          "image": "$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query 'properties.template.containers[0].image' -o tsv)",
          "command": ["python"],
          "args": $(echo "$JOIN_ARGS" | jq -Rc 'split(",")')
        }
      ]
    }
  }
}
EOF
  az rest --method PATCH \
    --url "https://management.azure.com$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query id -o tsv)?api-version=2024-10-02-preview" \
    --body "@$PATCH" \
    >/dev/null

  echo "==> Starting job"
  EXEC_NAME=$(az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query name -o tsv)
  echo "    execution:  $EXEC_NAME"
else
  EXEC_NAME=$(az containerapp job execution list \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query '[0].name' -o tsv)
  echo "==> Re-tailing latest execution: $EXEC_NAME"
fi

# Poll execution status (timeout 30 min).
START_EPOCH=$(date +%s)
TIMEOUT=$((30 * 60))
STATUS=""
while true; do
  STATUS=$(az containerapp job execution show \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --job-execution-name "$EXEC_NAME" \
    --query 'properties.status' -o tsv 2>/dev/null || echo 'Unknown')
  case "$STATUS" in
    Succeeded|Failed|Stopped|Cancelled|Degraded) break ;;
  esac
  NOW=$(date +%s)
  if (( NOW - START_EPOCH > TIMEOUT )); then
    echo "error: timeout after ${TIMEOUT}s waiting for execution $EXEC_NAME (last status=$STATUS)" >&2
    exit 3
  fi
  sleep 10
done
echo "==> Execution status: $STATUS"

# Tail logs from the execution's container. Log Analytics has ~30s ingestion
# lag, so wait briefly before the first query.
echo "==> Streaming logs (Log Analytics; ingestion lag ~30s)"
sleep 20
az monitor log-analytics query \
  --workspace "$LOG_WORKSPACE_ID" \
  --analytics-query "ContainerAppConsoleLogs_CL
    | where ContainerJobName_s == '${JOB_NAME}'
    | where ExecutionName_s == '${EXEC_NAME}'
    | order by TimeGenerated asc
    | project TimeGenerated, Log_s" \
  -o tsv 2>/dev/null \
  | sed 's/^/    /' \
  || echo "    (no logs returned yet — re-run with --update-only in ~30s if empty)"

case "$STATUS" in
  Succeeded) exit 0 ;;
  *) exit 4 ;;
esac
