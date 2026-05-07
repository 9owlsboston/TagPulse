#!/usr/bin/env bash
# scripts/azd-bootstrap.sh <env>
#
# Create deploy/azure/.env.<env> from the matching .env.<env>.example,
# generating fresh secrets and prompting only for values nobody can guess
# (subscription id, region). Optionally creates + selects the azd
# environment so the very next command can be `azd up`.
#
# Idempotent-ish: if the target file already exists, the script refuses
# to overwrite it and prints rotation instructions.

set -euo pipefail

ENV_NAME="${1:-}"
if [[ -z "$ENV_NAME" ]]; then
  cat >&2 <<EOF
Usage: $0 <env>

  env: dev | staging | prod  (or any other identifier you want to support)

Examples:
  $0 dev          # creates deploy/azure/.env.dev + azd env tagpulse-dev
  $0 staging
  $0 prod
EOF
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/deploy/azure/.env.${ENV_NAME}.example"
TARGET="$REPO_ROOT/deploy/azure/.env.${ENV_NAME}"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "error: template $TEMPLATE not found." >&2
  echo "       Add deploy/azure/.env.${ENV_NAME}.example first (copy from .env.dev.example)." >&2
  exit 1
fi

if [[ -f "$TARGET" ]]; then
  cat >&2 <<EOF
error: $TARGET already exists.

To rotate secrets in place, edit the file directly:
    \$EDITOR $TARGET

Then push the new values into azd:
    scripts/azd-env-load.sh $ENV_NAME
    azd deploy   # or 'azd provision' if KV-seeded secrets changed

To start over from scratch:
    rm $TARGET && $0 $ENV_NAME
EOF
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "error: openssl not on PATH (needed to generate secrets)" >&2
  exit 1
fi

# ── Prompt for things we can't generate ──────────────────────────────────────
# Sub id: try to default to whatever az is currently logged into.
DEFAULT_SUB=""
if command -v az >/dev/null 2>&1; then
  DEFAULT_SUB=$(az account show --query id -o tsv 2>/dev/null || true)
fi

if [[ -n "$DEFAULT_SUB" ]]; then
  read -r -p "Azure subscription id [$DEFAULT_SUB]: " SUB
  SUB="${SUB:-$DEFAULT_SUB}"
else
  read -r -p "Azure subscription id: " SUB
fi
if [[ ! "$SUB" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  echo "error: '$SUB' doesn't look like a UUID" >&2
  exit 1
fi

# Region: default to template's value
DEFAULT_REGION=$(grep -E '^AZURE_LOCATION=' "$TEMPLATE" | cut -d= -f2 | tr -d '"' || true)
DEFAULT_REGION="${DEFAULT_REGION:-southcentralus}"
read -r -p "Azure region [$DEFAULT_REGION]: " REGION
REGION="${REGION:-$DEFAULT_REGION}"

# ── Generate secrets ─────────────────────────────────────────────────────────
PG_PW=$(openssl rand -base64 32 | tr -d '\n=' | head -c 40)
JWT=$(openssl rand -hex 32)
MQTT_PW=$(openssl rand -base64 24 | tr -d '\n=' | head -c 32)

# ── Render the template into the target ──────────────────────────────────────
# sed handles base64 / hex values fine — the values never contain '|'.
# Use '|' as the sed delimiter to avoid escaping '/' in passwords.
sed \
  -e "s|^AZURE_SUBSCRIPTION_ID=.*|AZURE_SUBSCRIPTION_ID=$SUB|" \
  -e "s|^AZURE_LOCATION=.*|AZURE_LOCATION=$REGION|" \
  -e "s|^AZURE_POSTGRES_ADMIN_PASSWORD=.*|AZURE_POSTGRES_ADMIN_PASSWORD=$PG_PW|" \
  -e "s|^AZURE_JWT_SECRET=.*|AZURE_JWT_SECRET=$JWT|" \
  -e "s|^AZURE_MQTT_PASSWORD=.*|AZURE_MQTT_PASSWORD=$MQTT_PW|" \
  "$TEMPLATE" > "$TARGET"

chmod 600 "$TARGET"

echo
echo "✓ wrote $TARGET (mode 600)"
echo "  AZURE_SUBSCRIPTION_ID=$SUB"
echo "  AZURE_LOCATION=$REGION"
echo "  3 secrets generated (40c postgres / 64c jwt / 32c mqtt)"
echo

# ── Offer to create + select the matching azd environment ────────────────────
if ! command -v azd >/dev/null 2>&1; then
  cat <<EOF
Next steps (azd not on PATH — install: https://aka.ms/install-azd):
    azd env new $(grep -E '^AZURE_ENV_NAME=' "$TARGET" | cut -d= -f2)
    scripts/azd-env-load.sh $ENV_NAME
    azd up
EOF
  exit 0
fi

AZD_ENV=$(grep -E '^AZURE_ENV_NAME=' "$TARGET" | cut -d= -f2)
if azd env list 2>/dev/null | awk '{print $1}' | grep -qx "$AZD_ENV"; then
  echo "azd env '$AZD_ENV' already exists — selecting it"
  azd env select "$AZD_ENV"
else
  read -r -p "Create azd environment '$AZD_ENV' now? [Y/n] " yn
  yn="${yn:-Y}"
  if [[ "$yn" =~ ^[Yy]$ ]]; then
    azd env new "$AZD_ENV" --location "$REGION" --subscription "$SUB"
  fi
fi

echo
echo "Next:"
echo "    scripts/azd-env-load.sh $ENV_NAME"
echo "    azd up"
