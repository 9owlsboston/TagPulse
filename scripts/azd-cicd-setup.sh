#!/usr/bin/env bash
# scripts/azd-cicd-setup.sh <env>
#
# One-time Phase 4 setup: wire a GitHub Environment to deploy-azure.yml
# via OIDC federated credential (no long-lived secrets). Creates the
# Entra app registration + service principal, the federated credential
# scoped to the GitHub Environment, the RG-Contributor + ACR-AcrPush
# role assignments, the GitHub Environment, and the 5 environment
# variables the workflow reads.
#
# Idempotent: re-runs are safe -- existing app/SP/credential/role
# assignments are detected and reused.
#
# Prerequisites:
#   - `az login` and `az account set` to the target subscription
#   - `gh auth status` shows you signed in to the org that owns the repo
#   - `azd up <env>` already ran successfully (the RG + ACR must exist)
#
# Usage:
#   scripts/azd-cicd-setup.sh dev
#   scripts/azd-cicd-setup.sh staging
#   scripts/azd-cicd-setup.sh production

set -euo pipefail

ENV_NAME="${1:-}"
if [[ -z "$ENV_NAME" ]]; then
  cat >&2 <<EOF
Usage: $0 <env>

  env: dev | staging | production

Wires the named GitHub Environment to deploy-azure.yml via OIDC.
Run *after* the first 'azd up <env>' succeeds.
EOF
  exit 1
fi

# --- prerequisites ----------------------------------------------------------
command -v az >/dev/null || { echo "az CLI not found" >&2; exit 1; }
command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }

if ! az account show >/dev/null 2>&1; then
  echo "Not logged into az -- run 'az login' first" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Not logged into gh -- run 'gh auth login' first" >&2
  exit 1
fi

# --- constants --------------------------------------------------------------
REPO="9owlsboston/TagPulse"
APP_NAME="tagpulse-deploy-${ENV_NAME}"
# Map env -> RG name (production -> tagpulse-prod-rg per repo convention)
case "$ENV_NAME" in
  production) RG_SUFFIX=prod ;;
  *)          RG_SUFFIX="$ENV_NAME" ;;
esac
RG="tagpulse-${RG_SUFFIX}-rg"

SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)

echo "==> Configuring CI/CD for environment '${ENV_NAME}'"
echo "    Repo:           ${REPO}"
echo "    Subscription:   ${SUB}"
echo "    Resource group: ${RG}"
echo "    App name:       ${APP_NAME}"
echo

# --- sanity: RG + ACR must exist (azd up should have created them) ---------
if ! az group show --name "$RG" >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: resource group '$RG' does not exist.

Run 'azd up' for the '$ENV_NAME' environment first -- this script grants
roles on the RG + ACR, which only exist after the initial deploy.
EOF
  exit 1
fi

ACR=$(az acr list -g "$RG" --query '[0].name' -o tsv)
if [[ -z "$ACR" ]]; then
  echo "ERROR: no ACR found in $RG" >&2
  exit 1
fi
echo "    ACR:            ${ACR}"
echo

# --- 1. GitHub Environment -------------------------------------------------
echo "==> [1/5] GitHub Environment"
gh api -X PUT "/repos/${REPO}/environments/${ENV_NAME}" --silent
echo "    created/updated environment '${ENV_NAME}'"

# --- 2. Entra app + SP -----------------------------------------------------
echo "==> [2/5] Entra app registration + service principal"
APP_ID=$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv 2>/dev/null || true)
if [[ -z "$APP_ID" ]]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  echo "    created app: $APP_ID"
else
  echo "    reusing app: $APP_ID"
fi

if ! az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  az ad sp create --id "$APP_ID" >/dev/null
  echo "    created service principal"
else
  echo "    reusing service principal"
fi

# --- 3. Federated credential ----------------------------------------------
echo "==> [3/5] Federated credential (OIDC)"
FC_NAME="github-${ENV_NAME}"
FC_SUBJECT="repo:${REPO}:environment:${ENV_NAME}"
FC_EXISTS=$(az ad app federated-credential list --id "$APP_ID" \
  --query "[?name=='${FC_NAME}'] | [0].name" -o tsv 2>/dev/null || true)
if [[ -z "$FC_EXISTS" ]]; then
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"${FC_NAME}\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"${FC_SUBJECT}\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" >/dev/null
  echo "    created credential '${FC_NAME}' (subject: ${FC_SUBJECT})"
else
  echo "    reusing credential '${FC_NAME}'"
fi

# --- 4. RBAC ---------------------------------------------------------------
echo "==> [4/5] Role assignments"
RG_SCOPE="/subscriptions/${SUB}/resourceGroups/${RG}"
ACR_SCOPE="${RG_SCOPE}/providers/Microsoft.ContainerRegistry/registries/${ACR}"

# Resolve SP object id (assignee for role create can be appId, but show queries use objectId)
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

assign_role() {
  local role="$1"
  local scope="$2"
  if az role assignment list --assignee "$SP_OID" --scope "$scope" \
       --role "$role" --query '[0].id' -o tsv 2>/dev/null | grep -q .; then
    echo "    [$role] already assigned at ${scope##*/}"
  else
    az role assignment create --assignee "$APP_ID" --role "$role" \
      --scope "$scope" >/dev/null
    echo "    [$role] assigned at ${scope##*/}"
  fi
}

assign_role Contributor "$RG_SCOPE"
assign_role AcrPush     "$ACR_SCOPE"

# --- 5. GitHub Environment variables --------------------------------------
echo "==> [5/5] GitHub Environment variables"
gh variable set AZURE_CLIENT_ID       --env "$ENV_NAME" --body "$APP_ID"  --repo "$REPO"
gh variable set AZURE_TENANT_ID       --env "$ENV_NAME" --body "$TENANT"  --repo "$REPO"
gh variable set AZURE_SUBSCRIPTION_ID --env "$ENV_NAME" --body "$SUB"     --repo "$REPO"
gh variable set AZURE_RESOURCE_GROUP  --env "$ENV_NAME" --body "$RG"      --repo "$REPO"
gh variable set AZURE_ACR_NAME        --env "$ENV_NAME" --body "$ACR"     --repo "$REPO"
echo "    set 5 variables on environment '${ENV_NAME}'"

echo
echo "==> Done. Verify with:"
echo "    scripts/azd-cicd-verify.sh ${ENV_NAME}"
echo
echo "==> Test the workflow with an existing image tag:"
echo "    az acr repository show-tags -n ${ACR} --repository tagpulse-api -o tsv | head -3"
echo "    gh workflow run deploy-azure.yml -f environment=${ENV_NAME} -f image_tag=<tag>"
