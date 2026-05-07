#!/usr/bin/env bash
# scripts/azd-mqtt-build.sh
#
# Build + push docker/mosquitto.Dockerfile to ACR repo `tagpulse-mqtt`
# using `az acr build` (cloud build, no local Docker daemon needed).
#
# Why this exists: the Mosquitto broker runs on ACI, and azd has no ACI
# service host — so we can't add `mqtt` to azure.yaml's `services:` block.
# This hook runs from `preprovision` after ACR exists; the first ever
# `azd up` skips it (ACR doesn't exist yet) and `azd-image-check.sh`
# falls back to placeholder images. The next `azd up` builds + pushes
# the real image, image-check flips placeholders off, and the Bicep
# reprovisions the ACI on the real image.
#
# Idempotent. Wired as the second preprovision step in azure.yaml.

set -euo pipefail

get() { azd env get-value "$1" 2>/dev/null | tr -d '\r' || true; }

NAME_PREFIX=$(get AZURE_NAME_PREFIX)
RG=$(get AZURE_RESOURCE_GROUP)
SUB=$(get AZURE_SUBSCRIPTION_ID)
TAG=$(get AZURE_IMAGE_TAG)
[[ -z "$TAG" ]] && TAG="latest"

if [[ -z "$NAME_PREFIX" || -z "$RG" ]]; then
  echo "[mqtt-build] AZURE_NAME_PREFIX or AZURE_RESOURCE_GROUP unset — skipping (first provision)"
  exit 0
fi

if [[ -n "$SUB" ]]; then
  az account set --subscription "$SUB" >/dev/null 2>&1 || true
fi

ACR_NAME=$(az acr list -g "$RG" --query "[?starts_with(name, '${NAME_PREFIX}acr')].name | [0]" -o tsv 2>/dev/null || true)
if [[ -z "$ACR_NAME" ]]; then
  echo "[mqtt-build] no ACR in $RG yet — skipping (first provision)"
  exit 0
fi

# Skip if the tag is already in the registry. Avoids a 30s no-op build on
# every `azd up` after the image is current.
if az acr repository show-tags --name "$ACR_NAME" --repository tagpulse-mqtt \
     --query "contains(@, '$TAG')" -o tsv 2>/dev/null | grep -qi true; then
  echo "[mqtt-build] tagpulse-mqtt:$TAG already in $ACR_NAME — skipping"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[mqtt-build] az acr build → ${ACR_NAME}.azurecr.io/tagpulse-mqtt:${TAG}"
az acr build \
  --registry "$ACR_NAME" \
  --image "tagpulse-mqtt:${TAG}" \
  --image "tagpulse-mqtt:latest" \
  --file docker/mosquitto.Dockerfile \
  "$REPO_ROOT" >/dev/null
echo "[mqtt-build]   ✓ pushed tagpulse-mqtt:${TAG}"
