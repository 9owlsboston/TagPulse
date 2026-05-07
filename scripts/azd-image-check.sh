#!/usr/bin/env bash
# scripts/azd-image-check.sh
#
# Decide whether `azd provision` should use placeholder container images.
#
# Why: bicep references `${acr}/tagpulse-{api,worker,migrations}:${imageTag}`,
# but on first provision (or after a fresh teardown) those images don't exist
# yet — Container Apps creation then fails with `MANIFEST_UNKNOWN`. The
# `useImagePlaceholders` bicep param swaps in public mcr.microsoft.com images
# so provision succeeds; `azd deploy` later replaces them with the real
# images via `az containerapp update`.
#
# Behavior:
#   - If ACR doesn't exist yet, or the migrations image+tag isn't pushed,
#     set AZURE_USE_IMAGE_PLACEHOLDERS=true.
#   - Otherwise set it to false.
#
# Idempotent. Wired as a preprovision hook in azure.yaml.

set -euo pipefail

get() { azd env get-value "$1" 2>/dev/null | tr -d '\r' || true; }

NAME_PREFIX=$(get AZURE_NAME_PREFIX)
RG=$(get AZURE_RESOURCE_GROUP)
SUB=$(get AZURE_SUBSCRIPTION_ID)
TAG=$(get AZURE_IMAGE_TAG)
[[ -z "$TAG" ]] && TAG="latest"

if [[ -n "$SUB" ]]; then
  az account set --subscription "$SUB" >/dev/null 2>&1 || true
fi

set_placeholder() {
  local val="$1"
  echo "[image-check] AZURE_USE_IMAGE_PLACEHOLDERS=$val"
  azd env set AZURE_USE_IMAGE_PLACEHOLDERS "$val" >/dev/null
}

if [[ -z "$NAME_PREFIX" || -z "$RG" ]]; then
  echo "[image-check] AZURE_NAME_PREFIX or AZURE_RESOURCE_GROUP unset — assuming first provision"
  set_placeholder true
  exit 0
fi

# Find the ACR in the resource group (name varies — `${prefix}acr${uniqueSuffix}`).
ACR_NAME=$(az acr list -g "$RG" --query "[?starts_with(name, '${NAME_PREFIX}acr')].name | [0]" -o tsv 2>/dev/null || true)
if [[ -z "$ACR_NAME" ]]; then
  echo "[image-check] no ACR in $RG yet"
  set_placeholder true
  exit 0
fi

# Check whether tagpulse-migrations:$TAG exists. The migrations image is the
# canary because it's the smallest and built last; if it's there, the others
# are too (azd deploy pushes them in lockstep).
if az acr repository show-tags --name "$ACR_NAME" --repository tagpulse-migrations \
     --query "contains(@, '$TAG')" -o tsv 2>/dev/null | grep -qi true; then
  echo "[image-check] tagpulse-migrations:$TAG exists in $ACR_NAME"
  set_placeholder false
else
  echo "[image-check] tagpulse-migrations:$TAG missing from $ACR_NAME — using placeholders"
  set_placeholder true
fi
