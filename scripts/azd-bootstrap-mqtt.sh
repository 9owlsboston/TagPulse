#!/usr/bin/env bash
# scripts/azd-bootstrap-mqtt.sh [<env>]
#
# Seed the Mosquitto config + password file into the Azure Files share that
# backs the tagpulse-mqtt ACI. ACI cannot inject files into a volume on first
# boot, so this is a one-time post-`azd up` step (or whenever you rotate
# AZURE_MQTT_PASSWORD).
#
# All inputs are derived from the currently-selected azd environment:
#   - resource group           ← AZURE_RESOURCE_GROUP
#   - storage account          ← bicep output mqttStorageAccountName
#   - mqtt password            ← .env.<env>  (AZURE_MQTT_PASSWORD)
#   - container instance name  ← "tagpulse-mqtt" (fixed in mqtt.bicep)
#
# Usage:
#     scripts/azd-bootstrap-mqtt.sh           # use current azd env
#     scripts/azd-bootstrap-mqtt.sh dev       # explicit env
#
# Idempotent: re-running uploads fresh files and restarts the ACI.

set -euo pipefail

ENV_NAME="${1:-}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for cmd in az azd docker; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "error: $cmd not on PATH" >&2; exit 1
  }
done

# ── Select azd env ────────────────────────────────────────────────────────────
if [[ -n "$ENV_NAME" ]]; then
  AZD_ENV="tagpulse-${ENV_NAME}"
  azd env select "$AZD_ENV" >/dev/null 2>&1 || {
    echo "error: azd env '$AZD_ENV' not found. Run scripts/azd-bootstrap.sh $ENV_NAME first." >&2
    exit 1
  }
else
  AZD_ENV=$(azd env get-value AZURE_ENV_NAME 2>/dev/null || true)
  [[ -z "$AZD_ENV" ]] && {
    echo "error: no azd env selected. Pass an env name (dev|staging|prod) or run 'azd env select'." >&2
    exit 1
  }
  ENV_NAME="${AZD_ENV#tagpulse-}"
fi
echo "azd env: $AZD_ENV"

# ── Discover resources from azd outputs ──────────────────────────────────────
RG=$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || true)
SA=$(azd env get-value MQTT_STORAGE_ACCOUNT_NAME 2>/dev/null \
     || azd env get-value mqttStorageAccountName 2>/dev/null || true)

# Fallback: query Bicep deployment outputs directly
if [[ -z "$SA" ]]; then
  SA=$(az deployment sub list \
        --query "[?contains(name, '${AZD_ENV}')].properties.outputs.mqttStorageAccountName.value" \
        -o tsv 2>/dev/null | head -1 || true)
fi

if [[ -z "$RG" || -z "$SA" ]]; then
  echo "error: could not derive RG / storage account from azd env." >&2
  echo "       AZURE_RESOURCE_GROUP=$RG  storage=$SA" >&2
  echo "       Has 'azd up' completed for this env?" >&2
  exit 1
fi

ACI_NAME=$(azd env get-value MQTT_ACI_NAME 2>/dev/null || true)
ACI_NAME="${ACI_NAME:-tagpulse-mqtt}"

# ── Read MQTT password from .env file ─────────────────────────────────────────
ENV_FILE="$REPO_ROOT/deploy/azure/.env.${ENV_NAME}"
[[ -f "$ENV_FILE" ]] || {
  echo "error: $ENV_FILE not found." >&2
  exit 1
}

MQTT_PW=$(grep -E '^AZURE_MQTT_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')
[[ -z "$MQTT_PW" ]] && {
  echo "error: AZURE_MQTT_PASSWORD not set in $ENV_FILE" >&2
  exit 1
}

echo "  resource group: $RG"
echo "  storage:        $SA"
echo "  ACI:            $ACI_NAME"

# ── Generate config files in a temp dir ───────────────────────────────────────
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

cat > "$WORKDIR/mosquitto.conf" <<'EOF'
listener 1883
allow_anonymous false
password_file /mosquitto/config/mosquitto.passwd
persistence true
persistence_location /mosquitto/data/
EOF

# Use eclipse-mosquitto image to generate the password hash
echo "Generating password hash with mosquitto_passwd..."
docker run --rm -v "$WORKDIR":/work eclipse-mosquitto:2 \
  mosquitto_passwd -b -c /work/mosquitto.passwd tagpulse "$MQTT_PW" >/dev/null

# ── Upload to Azure Files share ──────────────────────────────────────────────
echo "Fetching storage account key..."
KEY=$(az storage account keys list -g "$RG" -n "$SA" --query '[0].value' -o tsv)

for f in mosquitto.conf mosquitto.passwd; do
  echo "Uploading $f to mosquitto-config share..."
  az storage file upload \
    --account-name "$SA" --account-key "$KEY" \
    --share-name mosquitto-config \
    --source "$WORKDIR/$f" \
    --only-show-errors >/dev/null
done

# ── Restart the ACI to pick up the seeded files ──────────────────────────────
echo "Restarting ACI '$ACI_NAME'..."
az container restart --name "$ACI_NAME" --resource-group "$RG" --only-show-errors

echo
echo "✓ MQTT broker bootstrap complete."
echo "  Verify: az container logs --name $ACI_NAME --resource-group $RG"
echo "          curl \"\$(azd env get-value SERVICE_API_URI)/health/ready\" | jq .checks.mqtt"
