#!/usr/bin/env bash
# scripts/azd-cicd-verify.sh <env>
#
# Read-only check that Phase 4 wiring is complete + correct for the
# named GitHub Environment. Run after azd-cicd-setup.sh, or any time
# you suspect drift (someone deleted a role, rotated an app, etc.).
#
# Exits 0 on success, non-zero with a diagnostic on the first failure.

set -euo pipefail

ENV_NAME="${1:-}"
if [[ -z "$ENV_NAME" ]]; then
  echo "Usage: $0 <env>   (dev | staging | production)" >&2
  exit 1
fi

REPO="9owlsboston/TagPulse"
APP_NAME="tagpulse-deploy-${ENV_NAME}"
case "$ENV_NAME" in
  production) RG_SUFFIX=prod ;;
  *)          RG_SUFFIX="$ENV_NAME" ;;
esac
RG="tagpulse-${RG_SUFFIX}-rg"

fail()  { echo "FAIL: $*" >&2; exit 1; }
ok()    { echo "  OK  $*"; }

echo "==> Verifying CI/CD wiring for environment '${ENV_NAME}'"

# 1. GitHub Environment exists
gh api "/repos/${REPO}/environments/${ENV_NAME}" >/dev/null 2>&1 \
  || fail "GitHub Environment '${ENV_NAME}' not found"
ok "GitHub Environment exists"

# 2. App registration + SP
APP_ID=$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)
[[ -n "$APP_ID" ]] || fail "Entra app '${APP_NAME}' not found"
ok "Entra app: $APP_ID"

az ad sp show --id "$APP_ID" >/dev/null 2>&1 || fail "Service principal missing"
ok "Service principal exists"

# 3. Federated credential
FC_SUBJECT="repo:${REPO}:environment:${ENV_NAME}"
FC=$(az ad app federated-credential list --id "$APP_ID" \
  --query "[?subject=='${FC_SUBJECT}'] | [0].name" -o tsv)
[[ -n "$FC" ]] || fail "Federated credential with subject '${FC_SUBJECT}' missing"
ok "Federated credential: $FC"

# 4. RBAC
SUB=$(az account show --query id -o tsv)
RG_SCOPE="/subscriptions/${SUB}/resourceGroups/${RG}"
az group show --name "$RG" >/dev/null 2>&1 || fail "Resource group '$RG' missing (run 'azd up' first)"

ACR=$(az acr list -g "$RG" --query '[0].name' -o tsv)
[[ -n "$ACR" ]] || fail "No ACR in $RG"
ACR_SCOPE="${RG_SCOPE}/providers/Microsoft.ContainerRegistry/registries/${ACR}"

SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
az role assignment list --assignee "$SP_OID" --scope "$RG_SCOPE" \
  --role Contributor --query '[0].id' -o tsv | grep -q . \
  || fail "Contributor role missing on $RG"
ok "Contributor on $RG"

az role assignment list --assignee "$SP_OID" --scope "$ACR_SCOPE" \
  --role AcrPush --query '[0].id' -o tsv | grep -q . \
  || fail "AcrPush role missing on $ACR"
ok "AcrPush on $ACR"

# 5. GitHub Environment variables
EXPECTED_VARS=(AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_ACR_NAME)
ACTUAL=$(gh variable list --env "$ENV_NAME" --repo "$REPO" --json name -q '.[].name' | sort)
for v in "${EXPECTED_VARS[@]}"; do
  echo "$ACTUAL" | grep -qx "$v" || fail "GitHub variable '$v' missing on environment '${ENV_NAME}'"
done
ok "All 5 GitHub variables set"

echo
echo "==> All checks passed. Ready to deploy:"
echo "    gh workflow run deploy-azure.yml -f environment=${ENV_NAME} -f image_tag=<tag>"
