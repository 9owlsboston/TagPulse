#!/usr/bin/env bash
# Phase 0 preflight check for the Azure first-deploy runbook
# (docs/runbooks/azure-first-deploy.md).
#
# Verifies that the local workstation + the active Azure subscription are
# ready for `scripts/azd-bootstrap.sh <env>` and `azd up`. Exits non-zero
# on any blocking failure; warnings ("WARN") are informational only.
#
# Usage:
#   scripts/azd-preflight.sh [--no-register]
#
# Reads the active subscription from `az account show`. By default,
# missing resource providers are auto-registered (async kickoff + poll
# up to ~5 min). Pass --no-register to only report them as failures.

set -u

AUTO_REGISTER=1
for arg in "$@"; do
  case "$arg" in
    --no-register) AUTO_REGISTER=0 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done
# We don't use -e: we want to keep checking after a failure and report
# every problem at the end.

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
FAILURES=()

green()  { printf '\033[32m%s\033[0m' "$1"; }
red()    { printf '\033[31m%s\033[0m' "$1"; }
yellow() { printf '\033[33m%s\033[0m' "$1"; }

ok()    { printf '  [%s] %s\n'   "$(green PASS)" "$1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail()  { printf '  [%s] %s\n'   "$(red FAIL)"  "$1"; FAIL_COUNT=$((FAIL_COUNT+1)); FAILURES+=("$1"); }
warn()  { printf '  [%s] %s\n'   "$(yellow WARN)" "$1"; WARN_COUNT=$((WARN_COUNT+1)); }

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# ---------- Tooling ----------
section "Tooling"

check_cmd() {
  local cmd="$1" min="${2:-}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    fail "$cmd not found in PATH"
    return
  fi
  if [[ -z "$min" ]]; then
    ok "$cmd present"
    return
  fi
  local v
  case "$cmd" in
    az)     v=$(az version --output tsv --query '"azure-cli"' 2>/dev/null) ;;
    azd)    v=$(azd version --output tsv 2>/dev/null | awk '/azd version/ {print $3}' || true)
            [[ -z "$v" ]] && v=$(azd version 2>/dev/null | awk '{print $3; exit}') ;;
    docker) v=$(docker version --format '{{.Client.Version}}' 2>/dev/null) ;;
    gh)     v=$(gh --version 2>/dev/null | awk 'NR==1 {print $3}') ;;
    openssl) v=$(openssl version 2>/dev/null | awk '{print $2}') ;;
    *)      v="" ;;
  esac
  if [[ -z "$v" ]]; then
    warn "$cmd present but version could not be parsed"
  else
    ok "$cmd $v (need >= $min)"
  fi
}

check_cmd az 2.60
check_cmd azd 1.10
check_cmd docker
check_cmd gh
check_cmd openssl

# ---------- Docker daemon ----------
section "Docker daemon"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable"
  else
    fail "docker daemon not responding (is Docker Desktop / dockerd running?)"
  fi
else
  warn "docker not installed; azd build steps will fail"
fi

# ---------- Azure auth ----------
section "Azure auth"
if ! command -v az >/dev/null 2>&1; then
  fail "skipping Azure checks: az CLI missing"
else
  if ! az account show >/dev/null 2>&1; then
    fail "not logged in (run 'az login')"
  else
    SUB_NAME=$(az account show --query name -o tsv)
    SUB_ID=$(az account show --query id -o tsv)
    TENANT=$(az account show --query tenantId -o tsv)
    ok "logged in to subscription '$SUB_NAME' ($SUB_ID)"
    ok "tenant: $TENANT"

    # azd auth (non-fatal; azd login can be done later)
    if azd auth login --check-status >/dev/null 2>&1; then
      ok "azd authenticated"
    else
      warn "azd not authenticated (run 'azd auth login')"
    fi

    # ---------- Resource provider registration ----------
    section "Resource provider registration"
    REQUIRED_RPS=(
      Microsoft.App
      Microsoft.ContainerRegistry
      Microsoft.DBforPostgreSQL
      Microsoft.OperationalInsights
      Microsoft.Insights
      Microsoft.KeyVault
      Microsoft.ContainerInstance
      Microsoft.Web
      Microsoft.Storage
      Microsoft.ManagedIdentity
    )
    # Azure normalises some namespaces to lowercase (e.g. 'microsoft.insights'),
    # so we compare case-insensitively.
    REGISTERED=$(az provider list --query "[?registrationState=='Registered'].namespace" -o tsv 2>/dev/null | tr '[:upper:]' '[:lower:]')
    MISSING_RPS=()
    for rp in "${REQUIRED_RPS[@]}"; do
      if grep -Fxq "${rp,,}" <<<"$REGISTERED"; then
        ok "$rp registered"
      else
        MISSING_RPS+=("$rp")
      fi
    done

    if (( ${#MISSING_RPS[@]} > 0 )); then
      if (( AUTO_REGISTER == 0 )); then
        for rp in "${MISSING_RPS[@]}"; do
          fail "$rp NOT registered (run: az provider register --namespace $rp)"
        done
      else
        echo "  Auto-registering ${#MISSING_RPS[@]} provider(s) (async, up to ~5 min)..."
        for rp in "${MISSING_RPS[@]}"; do
          if az provider register --namespace "$rp" >/dev/null 2>&1; then
            printf '    -> kicked off %s\n' "$rp"
          else
            fail "$rp registration kickoff failed (try manually: az provider register --namespace $rp)"
          fi
        done
        # Poll: 60 attempts * 5s = 5 min cap
        for attempt in $(seq 1 60); do
          STILL_MISSING=()
          # Lowercase both sides for case-insensitive lookup (see above).
          STATES=$(az provider list \
            --query "[].{n:namespace,s:registrationState}" \
            -o tsv 2>/dev/null | tr '[:upper:]' '[:lower:]')
          for rp in "${MISSING_RPS[@]}"; do
            state=$(awk -v n="${rp,,}" '$1==n {print $2; exit}' <<<"$STATES")
            [[ "$state" != "registered" ]] && STILL_MISSING+=("$rp")
          done
          if (( ${#STILL_MISSING[@]} == 0 )); then
            for rp in "${MISSING_RPS[@]}"; do ok "$rp registered (auto)"; done
            break
          fi
          if (( attempt == 60 )); then
            for rp in "${STILL_MISSING[@]}"; do
              fail "$rp still not Registered after 5 min (check: az provider show -n $rp --query registrationState)"
            done
            break
          fi
          printf '    waiting... %d still pending (attempt %d/60)\r' "${#STILL_MISSING[@]}" "$attempt"
          sleep 5
        done
        echo
      fi
    fi

    # ---------- RBAC on subscription ----------
    section "RBAC on subscription"
    UPN=$(az account show --query user.name -o tsv)
    UPN_TYPE=$(az account show --query user.type -o tsv)
    if [[ "$UPN_TYPE" == "servicePrincipal" ]]; then
      warn "running as service principal '$UPN' — assuming RBAC was granted out-of-band"
    else
      ROLES=$(az role assignment list \
        --assignee "$UPN" \
        --scope "/subscriptions/$SUB_ID" \
        --include-inherited \
        --query '[].roleDefinitionName' -o tsv 2>/dev/null)
      if grep -qE '^(Owner|Contributor)$' <<<"$ROLES"; then
        ok "have Owner or Contributor on subscription"
      else
        fail "need Owner or (Contributor + User Access Administrator) on subscription; found: $(echo "$ROLES" | tr '\n' ',' | sed 's/,$//')"
      fi
      if grep -q '^Owner$' <<<"$ROLES"; then
        ok "Owner role implies User Access Administrator"
      elif grep -q '^User Access Administrator$' <<<"$ROLES"; then
        ok "have User Access Administrator (required for role-assignment Bicep)"
      elif grep -q '^Contributor$' <<<"$ROLES"; then
        fail "Contributor alone cannot create role assignments — also need User Access Administrator"
      fi
    fi
  fi
fi

# ---------- Workspace state ----------
section "Workspace"
REPO_ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" ]]; then
  warn "not in a git checkout (skipping workspace checks)"
else
  for f in deploy/azure/.env.dev.example \
           deploy/azure/.env.staging.example \
           deploy/azure/.env.prod.example \
           scripts/azd-bootstrap.sh \
           scripts/azd-env-load.sh \
           azure.yaml \
           deploy/azure/bicep/main.bicep; do
    if [[ -e "$REPO_ROOT/$f" ]]; then
      ok "$f present"
    else
      fail "$f missing"
    fi
  done
fi

# ---------- Summary ----------
section "Summary"
printf '  %s passed, %s failed, %s warnings\n\n' \
  "$(green "$PASS_COUNT")" \
  "$( ((FAIL_COUNT==0)) && green 0 || red "$FAIL_COUNT" )" \
  "$( ((WARN_COUNT==0)) && green 0 || yellow "$WARN_COUNT" )"

if (( FAIL_COUNT > 0 )); then
  echo "Blocking failures:"
  for msg in "${FAILURES[@]}"; do
    echo "  - $msg"
  done
  echo
  echo "See docs/runbooks/azure-first-deploy.md § Phase 0 for fix steps."
  exit 1
fi

echo "Phase 0 checks passed. Next: scripts/azd-bootstrap.sh <env>"
exit 0
