#!/usr/bin/env bash
# scripts/azd-ui-token-rotate.sh <env> [--force]
#
# Sprint 25 D1. Rotates the Static Web App deployment token for env <env>
# and writes the new value into the TagPulse-UI repo's GitHub Environment
# secret (AZURE_STATIC_WEB_APPS_API_TOKEN).
#
# 1. `az staticwebapp secrets reset-api-key` — server-side rotation.
#    The old token is invalid the moment this returns; any in-flight
#    UI deploy using the old token will 401. Run during a low-traffic
#    window (the Sprint 22 deploy doc recommends 09:00 UTC).
# 2. Read the new token via `az staticwebapp secrets list` (re-uses the
#    Sprint 24 A1 retrieval path).
# 3. Pipe it into `gh -R 9owlsboston/TagPulse-UI secret set
#    AZURE_STATIC_WEB_APPS_API_TOKEN --env <env>`.
# 4. Print the last 4 characters of the new token to stdout for
#    operator audit (full token never leaves the gh-CLI pipe).
# 5. Append a structured audit-log entry to deploy/azure/.audit/
#    rotation.log (jsonl) so we can answer "when was X env last
#    rotated?" without going to the Azure portal.
#
# Idempotency / safety:
#   - The script refuses to run when the previous rotation was <60 days
#     ago, unless --force is passed. This is the safety guard against
#     a manually-triggered rotation racing the cron in D2.
#   - Read-only failure modes (missing azd env, wrong tenant) exit 1
#     before any state mutation.
#   - The rotation step is the only mutation; everything after it
#     either updates a remote secret or appends to the audit log.
#
# Required environment / state:
#   - `azd` env "tagpulse-${ENV_NAME}" must exist and have
#     AZURE_STATIC_WEB_APPS_NAME + AZURE_RESOURCE_GROUP populated.
#   - `gh` must be authenticated against an account that can write
#     secrets to 9owlsboston/TagPulse-UI (`gh auth status`).
#   - `az` must be signed in to the tenant that owns the SWA.
#
# Exit codes:
#   0 — rotation completed; new token written to UI repo + audit log.
#   1 — usage / auth / state error before any rotation happened.
#   2 — rotation succeeded but UI-repo secret update failed (manual
#       cleanup required: rotate again, or copy the token by hand).
#   3 — refused: previous rotation <60 days ago and --force not given.
#
# Usage:
#   scripts/azd-ui-token-rotate.sh dev
#   scripts/azd-ui-token-rotate.sh production --force

set -euo pipefail

ENV_NAME=""
FORCE=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
    *)
      if [[ -z "$ENV_NAME" ]]; then
        ENV_NAME="$arg"
      else
        echo "Unexpected positional arg: $arg" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$ENV_NAME" ]]; then
  echo "Usage: $0 <env> [--force]" >&2
  exit 1
fi

for cmd in az gh jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: $cmd CLI not found on PATH" >&2
    exit 1
  fi
done

# ---------- resolve SWA + resource group -------------------------------------
# Two paths:
#   1) interactive workstation: read from `azd env get-value` (mirrors
#      scripts/azd-ui-token.sh).
#   2) GHA cron (D2): the runner has no persisted azd env state, so the
#      caller may export AZURE_STATIC_WEB_APPS_NAME + AZURE_RESOURCE_GROUP
#      directly. The env-var path takes precedence and skips the azd
#      preflight entirely.
SWA_NAME="${AZURE_STATIC_WEB_APPS_NAME:-}"
RG_NAME="${AZURE_RESOURCE_GROUP:-}"

if [[ -z "$SWA_NAME" || -z "$RG_NAME" ]]; then
  if ! command -v azd >/dev/null 2>&1; then
    echo "error: azd CLI not found and AZURE_STATIC_WEB_APPS_NAME / AZURE_RESOURCE_GROUP not set in env" >&2
    exit 1
  fi
  get() {
    local v
    if v=$(azd -e "tagpulse-${ENV_NAME}" env get-value "$1" 2>/dev/null); then
      printf '%s' "$v" | tr -d '\r'
    fi
  }
  [[ -z "$SWA_NAME" ]] && SWA_NAME="$(get AZURE_STATIC_WEB_APPS_NAME)"
  [[ -z "$RG_NAME" ]] && RG_NAME="$(get AZURE_RESOURCE_GROUP)"
fi

if [[ -z "$SWA_NAME" || -z "$RG_NAME" ]]; then
  cat >&2 <<EOF
error: cannot resolve SWA + resource group for env=${ENV_NAME}.
       Either run 'azd provision' against ${ENV_NAME} at least once, or
       export AZURE_STATIC_WEB_APPS_NAME and AZURE_RESOURCE_GROUP before
       invoking this script.
EOF
  exit 1
fi

# ---------- 60-day idempotency gate ------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUDIT_DIR="${REPO_ROOT}/deploy/azure/.audit"
AUDIT_LOG="${AUDIT_DIR}/ui-token-rotation.jsonl"
mkdir -p "$AUDIT_DIR"

if [[ -f "$AUDIT_LOG" && "$FORCE" -eq 0 ]]; then
  LAST_TS_ISO="$(grep "\"env\":\"${ENV_NAME}\"" "$AUDIT_LOG" \
    | tail -1 | jq -r '.timestamp // empty' 2>/dev/null || true)"
  if [[ -n "$LAST_TS_ISO" ]]; then
    # GNU date; busybox/macOS users should run with --force.
    LAST_EPOCH=$(date -u -d "$LAST_TS_ISO" +%s 2>/dev/null || echo 0)
    NOW_EPOCH=$(date -u +%s)
    AGE_DAYS=$(( (NOW_EPOCH - LAST_EPOCH) / 86400 ))
    if [[ "$LAST_EPOCH" -gt 0 && "$AGE_DAYS" -lt 60 ]]; then
      cat >&2 <<EOF
refusing: env ${ENV_NAME} was rotated ${AGE_DAYS} days ago (<60 days).
         Pass --force to rotate anyway. Last rotation: ${LAST_TS_ISO}.
EOF
      exit 3
    fi
  fi
fi

# ---------- rotate -----------------------------------------------------------
echo "==> Rotating apiKey for SWA ${SWA_NAME} in ${RG_NAME}..." >&2
if [[ "$DRY_RUN" -eq 1 ]]; then
  cat >&2 <<EOF
DRY-RUN: would run:
  az staticwebapp secrets reset-api-key --name $SWA_NAME --resource-group $RG_NAME
  az staticwebapp secrets list         --name $SWA_NAME --resource-group $RG_NAME --query properties.apiKey
  printf '%s' "<new-token>" | gh -R ${TAGPULSE_UI_REPO:-9owlsboston/TagPulse-UI} secret set AZURE_STATIC_WEB_APPS_API_TOKEN --env $ENV_NAME --body -
  jq … >> deploy/azure/.audit/ui-token-rotation.jsonl
EOF
  exit 0
fi
az staticwebapp secrets reset-api-key \
  --name "$SWA_NAME" \
  --resource-group "$RG_NAME" \
  --output none

# Re-read the new token. `secrets list` returns the current (post-reset)
# value; the reset-api-key call itself does not echo it back.
NEW_TOKEN="$(az staticwebapp secrets list \
  --name "$SWA_NAME" \
  --resource-group "$RG_NAME" \
  --query 'properties.apiKey' \
  -o tsv 2>/dev/null || true)"

if [[ -z "$NEW_TOKEN" ]]; then
  echo "error: rotation succeeded but failed to read the new apiKey." >&2
  echo "       Run 'scripts/azd-ui-token.sh ${ENV_NAME} --print' and" >&2
  echo "       update the UI repo secret manually." >&2
  exit 2
fi

# ---------- update UI repo GitHub Environment secret -------------------------
UI_REPO="${TAGPULSE_UI_REPO:-9owlsboston/TagPulse-UI}"
SECRET_NAME="AZURE_STATIC_WEB_APPS_API_TOKEN"
echo "==> Writing new token to ${UI_REPO} env=${ENV_NAME} secret=${SECRET_NAME}..." >&2
if ! printf '%s' "$NEW_TOKEN" | gh -R "$UI_REPO" secret set "$SECRET_NAME" \
    --env "$ENV_NAME" --body - >/dev/null 2>&1; then
  cat >&2 <<EOF
error: rotation succeeded on Azure side, but 'gh secret set' failed.
       The UI repo's ${ENV_NAME} environment still holds the old token.
       Manual cleanup:
         scripts/azd-ui-token.sh ${ENV_NAME} --print | \\
           gh -R ${UI_REPO} secret set ${SECRET_NAME} --env ${ENV_NAME}
EOF
  exit 2
fi

# ---------- audit ------------------------------------------------------------
LAST4="${NEW_TOKEN: -4}"
NOW="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
ACTOR="${GITHUB_ACTOR:-$(whoami)}"
RUN_ID="${GITHUB_RUN_ID:-local}"
jq -nc \
  --arg ts "$NOW" \
  --arg env "$ENV_NAME" \
  --arg swa "$SWA_NAME" \
  --arg rg "$RG_NAME" \
  --arg repo "$UI_REPO" \
  --arg actor "$ACTOR" \
  --arg run_id "$RUN_ID" \
  --arg last4 "$LAST4" \
  '{timestamp:$ts, env:$env, swa:$swa, resource_group:$rg,
    ui_repo:$repo, actor:$actor, run_id:$run_id,
    new_token_last4:$last4, action:"rotated"}' >> "$AUDIT_LOG"

cat <<EOF
✓ rotated ${ENV_NAME}
  swa            : ${SWA_NAME}
  resource_group : ${RG_NAME}
  ui_repo_secret : ${UI_REPO}/${ENV_NAME}/${SECRET_NAME}
  new_token_last4: ${LAST4}
  audit_log      : ${AUDIT_LOG}

Next: trigger a UI deploy against env=${ENV_NAME} to verify the new token
      (e.g. push a no-op commit to the UI repo's main branch, or run
       'gh workflow run deploy-azure.yml -R ${UI_REPO} --ref main').
EOF
