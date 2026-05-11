# scripts/lib/azd-common.sh — shared helpers for the azd-*.sh script suite.
#
# Source this file from any operator-facing shell script:
#
#     source "$(dirname "${BASH_SOURCE[0]}")/lib/azd-common.sh"
#
# Provides:
#   - azd               — wrapper that swallows the "your version of azd is out
#                         of date" upgrade nag (set AZD_OUTDATED=1 if seen).
#   - azd_env_resolve <env> — accepts shorthand ('dev') or full ('tagpulse-dev'),
#                         exports RESOLVED_AZD_ENV, RG, KV_NAME, ACR_NAME,
#                         CONTAINER_APPS_ENV_NAME, JOB_NAME, LOG_WORKSPACE_ID,
#                         SUBSCRIPTION_ID. Errors and exits 2 on missing env.
#   - aca_name <env> <service> — resolves a Container App / Job name by listing
#                         resources in $RG and matching `tp${env}-${service}`
#                         (api | worker | mqtt | migrations-job | tools-job |
#                         migrations | tools).
#   - kv_secret_get <name> [--env <env>] — single-shot wrapper around
#                         `az keyvault secret show`. Requires azd_env_resolve
#                         to have been called (or pass --env).
#   - require_clean_tree — refuse to run when working tree is dirty or unpushed
#                         commits exist. Override with --allow-stale on the
#                         caller (sets ALLOW_STALE=1 before sourcing).
#   - die <msg> [exit_code] — print to stderr, exit with code (default 1).
#   - log <msg>         — timestamped stderr log line.
#
# Convention: helpers exit 2 for "operator setup wrong" (missing env, wrong
# context); 1 for "command failed at runtime"; 0 for success.

# -----------------------------------------------------------------------------
# azd wrapper — silences the upgrade nag.
# -----------------------------------------------------------------------------
AZD_OUTDATED=0
azd() {
  local out
  if ! out=$(command azd "$@" 2>&1); then
    local rc=$?
    printf '%s\n' "$out" >&2
    return $rc
  fi
  if printf '%s' "$out" | grep -q 'out of date'; then
    AZD_OUTDATED=1
    out=$(printf '%s' "$out" | grep -vE \
      'out of date|aka\.ms/install-azd|aka\.ms/azd/upgrade|To update to the latest|^If the install script|^curl -fsSL|^$')
  fi
  [[ -n "$out" ]] && printf '%s\n' "$out"
}

# -----------------------------------------------------------------------------
# Stderr helpers.
# -----------------------------------------------------------------------------
log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2
}

die() {
  local msg="$1"
  local code="${2:-1}"
  printf 'error: %s\n' "$msg" >&2
  exit "$code"
}

# -----------------------------------------------------------------------------
# require_clean_tree — guard against running deployed-image scripts with local
# uncommitted changes the operator might think are live.
# -----------------------------------------------------------------------------
require_clean_tree() {
  if [[ "${ALLOW_STALE:-0}" == "1" ]]; then
    log "skipping clean-tree check (ALLOW_STALE=1)"
    return 0
  fi
  if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
    die "Working tree is dirty. Commit or stash before running (or set ALLOW_STALE=1)." 2
  fi
  local local_head upstream_head
  local_head=$(git rev-parse HEAD 2>/dev/null || echo '')
  upstream_head=$(git rev-parse '@{u}' 2>/dev/null || echo '')
  if [[ -n "$upstream_head" && "$local_head" != "$upstream_head" ]]; then
    # Allow ahead-of-upstream only if the upstream is reachable from HEAD
    # (i.e. we're ahead, not diverged).
    if ! git merge-base --is-ancestor "$upstream_head" "$local_head" 2>/dev/null; then
      die "Local branch has diverged from upstream. Push or pull before running (or set ALLOW_STALE=1)." 2
    fi
    log "warning: local branch is ahead of upstream — deployed image runs the pushed code."
  fi
}

# -----------------------------------------------------------------------------
# azd_env_resolve <env> — load every common env-derived value into globals.
# Accepts 'dev' / 'staging' / 'prod' shorthand, or a full 'tagpulse-…' name.
# Exports: RESOLVED_AZD_ENV, RG, KV_NAME, ACR_NAME, CONTAINER_APPS_ENV_NAME,
#          JOB_NAME, LOG_WORKSPACE_ID, SUBSCRIPTION_ID, ENV_SHORT.
# -----------------------------------------------------------------------------
azd_env_resolve() {
  local env_name="${1:-}"
  [[ -z "$env_name" ]] && die "azd_env_resolve: <env> required (e.g. dev)" 2

  if azd env select "$env_name" >/dev/null 2>&1; then
    RESOLVED_AZD_ENV="$env_name"
  elif azd env select "tagpulse-$env_name" >/dev/null 2>&1; then
    RESOLVED_AZD_ENV="tagpulse-$env_name"
  else
    die "azd env '$env_name' (or 'tagpulse-$env_name') not found. Run 'azd env list'." 2
  fi

  # ENV_SHORT: 'dev' / 'staging' / 'prod' — strip any 'tagpulse-' prefix.
  ENV_SHORT="${RESOLVED_AZD_ENV#tagpulse-}"

  RG="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo '')"
  KV_NAME="$(azd env get-value AZURE_KEYVAULT_NAME 2>/dev/null || true)"
  [[ -z "$KV_NAME" ]] && KV_NAME="$(azd env get-value keyVaultName 2>/dev/null || echo '')"
  ACR_NAME="$(azd env get-value AZURE_CONTAINER_REGISTRY_NAME 2>/dev/null || true)"
  [[ -z "$ACR_NAME" ]] && ACR_NAME="$(azd env get-value containerRegistryName 2>/dev/null || true)"
  # Sprint 28+ Bicep emits the output as `acrName`; older deploys used the
  # other two names above. Keep all three lookups.
  [[ -z "$ACR_NAME" ]] && ACR_NAME="$(azd env get-value acrName 2>/dev/null || true)"
  # Last-ditch: derive from the login-server FQDN if only that is set.
  if [[ -z "$ACR_NAME" ]]; then
    local _login
    _login="$(azd env get-value AZURE_ACR_LOGIN_SERVER 2>/dev/null || azd env get-value acrLoginServer 2>/dev/null || echo '')"
    [[ -n "$_login" ]] && ACR_NAME="${_login%%.*}"
  fi
  CONTAINER_APPS_ENV_NAME="$(azd env get-value containerAppsEnvName 2>/dev/null || echo '')"
  JOB_NAME="$(azd env get-value toolsJobName 2>/dev/null || echo '')"
  SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID 2>/dev/null || echo '')"

  if [[ -z "$RG" ]]; then
    die "azd env '$RESOLVED_AZD_ENV' is missing AZURE_RESOURCE_GROUP. Has 'azd up' been run?" 2
  fi

  # LOG_WORKSPACE_ID is read-only / queryable from ACA env once we have it.
  if [[ -n "$CONTAINER_APPS_ENV_NAME" ]]; then
    LOG_WORKSPACE_ID="$(az containerapp env show \
      --name "$CONTAINER_APPS_ENV_NAME" \
      --resource-group "$RG" \
      --query 'properties.appLogsConfiguration.logAnalyticsConfiguration.customerId' \
      -o tsv 2>/dev/null || echo '')"
  else
    LOG_WORKSPACE_ID=""
  fi

  export RESOLVED_AZD_ENV ENV_SHORT RG KV_NAME ACR_NAME \
         CONTAINER_APPS_ENV_NAME JOB_NAME LOG_WORKSPACE_ID SUBSCRIPTION_ID
}

# -----------------------------------------------------------------------------
# aca_name <env> <service> — resolve the *runtime* name of a Container App or
# Job in the env's RG. Several Sprint 27/28 fixes had to discover this at
# runtime (`839ad03`, `2c2957d`, `0ed74f2`) because Bicep outputs alone aren't
# always loaded into azd env (image-only deploys, partial deploys).
#
# Strategy: list resources in $RG, filter by the canonical `tp${env}-${kind}`
# prefix, return the first match. For 'tools'/'migrations' jobs the prefix is
# slightly different (`tools-job-${env}` / `tp${env}-migrations`).
# -----------------------------------------------------------------------------
aca_name() {
  local env_name="${1:-}"
  local kind="${2:-}"
  [[ -z "$env_name" || -z "$kind" ]] && die "aca_name <env> <service>" 2

  # Normalize env (strip 'tagpulse-' if passed as full).
  local env_short="${env_name#tagpulse-}"
  local rg="${RG:-}"
  if [[ -z "$rg" ]]; then
    rg="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || echo '')"
    [[ -z "$rg" ]] && die "aca_name: RG not set (call azd_env_resolve first)" 2
  fi

  case "$kind" in
    api|worker)
      # Container App: tp${env}-${kind}
      az containerapp list -g "$rg" \
        --query "[?starts_with(name, 'tp${env_short}-${kind}')].name | [0]" -o tsv 2>/dev/null
      ;;
    mqtt)
      # mqtt may be deployed as a Container App OR an Azure Container
      # Instance (ACI) depending on the env (Sprint 28 dev uses ACI inside
      # the ACA VNet). Try ACA first, then ACI.
      local n
      n=$(az containerapp list -g "$rg" \
        --query "[?starts_with(name, 'tp${env_short}-mqtt')].name | [0]" -o tsv 2>/dev/null)
      if [[ -z "$n" ]]; then
        n=$(az container list -g "$rg" \
          --query "[?starts_with(name, 'tp${env_short}-mqtt')].name | [0]" -o tsv 2>/dev/null)
      fi
      printf '%s' "$n"
      ;;
    migrations|migrations-job)
      # Job: tp${env}-migrations OR tp${env}-migrations-job — try both.
      local n
      n=$(az containerapp job list -g "$rg" \
        --query "[?starts_with(name, 'tp${env_short}-migrations')].name | [0]" -o tsv 2>/dev/null)
      printf '%s' "$n"
      ;;
    tools|tools-job)
      # Sprint 26 used 'tools-job-${env}'; allow either pattern.
      local n
      n=$(az containerapp job list -g "$rg" \
        --query "[?starts_with(name, 'tools-job-${env_short}') || starts_with(name, 'tp${env_short}-tools')].name | [0]" -o tsv 2>/dev/null)
      printf '%s' "$n"
      ;;
    *)
      die "aca_name: unknown service kind '$kind' (api|worker|mqtt|migrations|tools)" 2
      ;;
  esac
}

# -----------------------------------------------------------------------------
# kv_secret_get <name> — read a KV secret by name. Requires azd_env_resolve
# to have been called (KV_NAME global set), or pass --env <env> first.
# -----------------------------------------------------------------------------
kv_secret_get() {
  local name=""
  local env_name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env)
        env_name="$2"; shift 2 ;;
      *)
        name="$1"; shift ;;
    esac
  done
  [[ -z "$name" ]] && die "kv_secret_get <name> [--env <env>]" 2
  if [[ -n "$env_name" ]]; then
    azd_env_resolve "$env_name"
  fi
  [[ -z "${KV_NAME:-}" ]] && die "kv_secret_get: KV_NAME not set (call azd_env_resolve first)" 2

  az keyvault secret show --vault-name "$KV_NAME" --name "$name" --query value -o tsv 2>/dev/null \
    || die "Failed to read KV secret '$name' from vault '$KV_NAME'. Check RBAC + firewall."
}
