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
#   --allow-stale   Allow running despite a dirty working tree / unpushed
#                   commits, OR a tools-job image whose tag differs from the
#                   deployed api app. By default we refuse both, because the
#                   job runs the *deployed* image — local script edits (or a
#                   skipped deploy) won't take effect until the job image is
#                   refreshed.
#
# Exit codes:
#   0   job execution Succeeded
#   1   usage / preflight (missing tool, dirty tree without --allow-stale)
#   2   azd env / Azure auth failure
#   3   job did not reach Succeeded within 30 minutes
#   4   job execution finished with non-Succeeded status (Failed/Stopped/etc)
#   5   tools-job image is stale vs the deployed api app (without --allow-stale)

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
#
# Accept either the shorthand ("dev") or the literal azd env name
# ("tagpulse-dev"), matching the convention `scripts/azd-bootstrap.sh`
# established (the bootstrap creates `tagpulse-<env>` from shorthand
# `<env>`). We try the literal first, then prefix `tagpulse-` if that fails.
echo "==> Resolving env $ENV_NAME"
if azd env select "$ENV_NAME" >/dev/null 2>&1; then
  RESOLVED_ENV="$ENV_NAME"
elif azd env select "tagpulse-$ENV_NAME" >/dev/null 2>&1; then
  RESOLVED_ENV="tagpulse-$ENV_NAME"
  echo "    (resolved shorthand '$ENV_NAME' → azd env '$RESOLVED_ENV')"
else
  echo "error: azd env '$ENV_NAME' (or 'tagpulse-$ENV_NAME') not found. Run 'azd env list'." >&2
  exit 2
fi
JOB_NAME="$(azd env get-value toolsJobName 2>/dev/null || echo '')"
RG="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo '')"
if [[ -z "$JOB_NAME" || -z "$RG" ]]; then
  echo "error: azd env $ENV_NAME is missing toolsJobName / AZURE_RESOURCE_GROUP." >&2
  echo "       Has 'azd up' been run against this env since Sprint 26 B2 landed?" >&2
  exit 2
fi
echo "    job:        $JOB_NAME"
echo "    rg:         $RG"
# Point the az CLI at the env's subscription. Without this, every `az` call
# below targets whatever subscription happens to be the laptop's default —
# which, in a multi-subscription tenant, is frequently NOT the one the dev
# resources live in, producing a confusing `ResourceGroupNotFound` (or an
# empty Log Analytics workspace id) even though the RG is perfectly healthy.
SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID 2>/dev/null || echo '')"
if [[ -n "$SUBSCRIPTION_ID" ]]; then
  az account set --subscription "$SUBSCRIPTION_ID" >/dev/null 2>&1 || {
    echo "error: 'az account set --subscription $SUBSCRIPTION_ID' failed." >&2
    echo "       Are you logged in (az login) with access to that subscription?" >&2
    exit 2
  }
  echo "    sub:        $SUBSCRIPTION_ID"
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
  # Staleness guard (defense-in-depth). `deploy-azure.yml` pins the tools-job
  # to the api image on every deploy, but if that step is ever skipped/broken
  # the job silently re-runs months-old code (this script re-uses whatever
  # image the job currently carries). Compare the job's image tag against the
  # api container app's live tag — they should match. A mismatch means a script
  # change you just merged is NOT yet on the job; warn loudly rather than run
  # stale code that looks like it succeeded.
  JOB_IMAGE_NOW=$(az containerapp job show -n "$JOB_NAME" -g "$RG" \
    --query 'properties.template.containers[0].image' -o tsv 2>/dev/null || echo '')
  API_APP=$(az containerapp list -g "$RG" \
    --query "[?ends_with(name,'-api')].name | [0]" -o tsv 2>/dev/null || echo '')
  API_IMAGE_NOW=""
  if [[ -n "$API_APP" ]]; then
    API_IMAGE_NOW=$(az containerapp show -n "$API_APP" -g "$RG" \
      --query 'properties.template.containers[0].image' -o tsv 2>/dev/null || echo '')
  fi
  if [[ -n "$JOB_IMAGE_NOW" && -n "$API_IMAGE_NOW" && \
        "${JOB_IMAGE_NOW##*:}" != "${API_IMAGE_NOW##*:}" ]]; then
    echo "WARNING: tools-job image tag (${JOB_IMAGE_NOW##*:}) != deployed api tag (${API_IMAGE_NOW##*:})." >&2
    echo "         The job may run stale code. To refresh it to the current api image:" >&2
    echo "         az containerapp job update -n $JOB_NAME -g $RG --image $API_IMAGE_NOW" >&2
    if [[ "$ALLOW_STALE" -eq 0 ]]; then
      echo "         (pass --allow-stale to run anyway)" >&2
      exit 5
    fi
    echo "         --allow-stale set; continuing with the stale job image." >&2
  fi
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
  #
  # ARM PATCH replaces the container[0] object wholesale (matched by `name`),
  # so we MUST round-trip the existing `env` array — otherwise every Bicep-
  # declared env var (DATABASE_URL, TAGPULSE_SMOKE_DB_URL, TAGPULSE_API_URL,
  # TAGPULSE_SMOKE_KEY_VAULT_NAME, …) gets wiped and the script falls back
  # to `localhost:5432`. Same applies to `resources`.
  EXISTING_IMAGE=$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query 'properties.template.containers[0].image' -o tsv)
  EXISTING_ENV=$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query 'properties.template.containers[0].env' -o json)
  EXISTING_RESOURCES=$(az containerapp job show -n "$JOB_NAME" -g "$RG" --query 'properties.template.containers[0].resources' -o json)
  PATCH=$(mktemp)
  trap 'rm -f "$PATCH"' EXIT
  jq -n \
    --arg image "$EXISTING_IMAGE" \
    --argjson env "$EXISTING_ENV" \
    --argjson resources "$EXISTING_RESOURCES" \
    --argjson args "$(echo "$JOIN_ARGS" | jq -Rc 'split(",")')" \
    '{
      properties: {
        template: {
          containers: [
            {
              name: "tools",
              image: $image,
              command: ["python"],
              args: $args,
              env: $env,
              resources: $resources
            }
          ]
        }
      }
    }' >"$PATCH"
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

# Stream logs. We try the Container Apps data-plane endpoint first
# (`az containerapp job logs show --follow`) — it's instant when it works,
# but for short-lived jobs (e.g. `get_kv_secret.py`, ~2s wall-clock) the
# replica is often gone before --follow can attach, returning nothing.
#
# Fall back to Log Analytics with a proper retry loop. Ingestion lag is
# typically 60–120s in this region; we retry every 20s for up to 4 minutes
# before giving up. Past wrapper versions polled once after sleep 20 and
# routinely lost output for short jobs — see CHANGELOG / runbook.
#
# We also wait for execution status to reach a terminal state before tailing,
# so partial-output races (job exits mid-query) can't happen.
echo "==> Polling execution status (timeout 30 min)"
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
  sleep 5
done
echo "==> Execution status: $STATUS"

# Try data-plane log stream first. Suppress preview-extension warnings to keep
# stdout clean (azd-kv-get.sh greps between sentinels).
echo "==> Fetching logs (data-plane first, Log Analytics fallback)"
DATAPLANE_OUT="$(az containerapp job logs show \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container tools \
  --execution "$EXEC_NAME" \
  --format text \
  --tail 300 \
  --only-show-errors 2>/dev/null || true)"

if [[ -n "$DATAPLANE_OUT" ]]; then
  # Data-plane format is "<RFC3339-timestamp> <stream> F <message>". Strip
  # the prefix so callers get the bare log line (azd-kv-get.sh greps
  # between sentinels and expects the bare value, not a timestamped line).
  printf '%s\n' "$DATAPLANE_OUT" \
    | sed -E 's/^[0-9TZ:.+-]+ +(stdout|stderr) +F +//' \
    | sed 's/^/    /'
else
  # Fall back to Log Analytics with retry. Ingestion lag is highly variable.
  LA_QUERY="ContainerAppConsoleLogs_CL
    | where ContainerJobName_s == '${JOB_NAME}'
    | where ExecutionName_s == '${EXEC_NAME}'
    | order by TimeGenerated asc
    | project Log_s"
  LA_OUT=""
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 20
    LA_OUT="$(az monitor log-analytics query \
      --workspace "$LOG_WORKSPACE_ID" \
      --analytics-query "$LA_QUERY" \
      -o tsv 2>/dev/null | awk -F'\t' '{print $1}' || true)"
    if [[ -n "$LA_OUT" ]]; then
      break
    fi
    echo "    (no logs yet — attempt $attempt/12, retrying in 20s)"
  done
  if [[ -n "$LA_OUT" ]]; then
    printf '%s\n' "$LA_OUT" | sed 's/^/    /'
  else
    echo "    (no logs returned after 4 minutes; re-run with --update-only later)"
  fi
fi

case "$STATUS" in
  Succeeded) exit 0 ;;
  *) exit 4 ;;
esac
