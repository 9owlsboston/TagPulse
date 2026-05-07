#!/usr/bin/env bash
# scripts/azd-env-load.sh <env>
#
# Push every non-empty value from deploy/azure/.env.<env> into the
# matching azd environment (tagpulse-<env>). If the azd env doesn't
# exist yet, the script offers to create it.
#
# Usage:
#     scripts/azd-env-load.sh dev
#     scripts/azd-env-load.sh staging
#     scripts/azd-env-load.sh prod
#
# Compatibility: also accepts a direct file path.

set -euo pipefail

# Wrap azd to drop the "your version of azd is out of date" upgrade nag
# that gets re-emitted on every invocation. Show it once at the end if
# detected, instead of spamming stderr per env-var.
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
    out=$(printf '%s' "$out" | grep -vE 'out of date|aka\.ms/install-azd|aka\.ms/azd/upgrade|To update to the latest|^If the install script|^curl -fsSL|^$')
  fi
  [[ -n "$out" ]] && printf '%s\n' "$out"
}

ARG="${1:-}"
if [[ -z "$ARG" ]]; then
  echo "Usage: $0 <env>   # e.g. dev | staging | prod" >&2
  echo "       $0 <path>  # path to a .env file" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -f "$ARG" ]]; then
  ENV_FILE="$ARG"
elif [[ -f "$REPO_ROOT/deploy/azure/.env.${ARG}" ]]; then
  ENV_FILE="$REPO_ROOT/deploy/azure/.env.${ARG}"
else
  cat >&2 <<EOF
error: no .env file found for '$ARG'
       tried: $REPO_ROOT/deploy/azure/.env.${ARG}
              $ARG (as path)

Bootstrap a new environment with:
    scripts/azd-bootstrap.sh $ARG
EOF
  exit 1
fi

if ! command -v azd >/dev/null 2>&1; then
  echo "error: azd not on PATH (install: https://aka.ms/install-azd)" >&2
  exit 1
fi

# Switch to the matching azd env if AZURE_ENV_NAME is in the file
TARGET_AZD_ENV=$(grep -E '^AZURE_ENV_NAME=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '"' || true)
if [[ -n "$TARGET_AZD_ENV" ]]; then
  if azd env list 2>/dev/null | awk '{print $1}' | grep -qx "$TARGET_AZD_ENV"; then
    azd env select "$TARGET_AZD_ENV" >/dev/null
    echo "azd env: $TARGET_AZD_ENV (selected)"
  else
    read -r -p "azd env '$TARGET_AZD_ENV' doesn't exist — create it now? [Y/n] " yn
    yn="${yn:-Y}"
    if [[ "$yn" =~ ^[Yy]$ ]]; then
      LOC=$(grep -E '^AZURE_LOCATION=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '"' || true)
      SUB=$(grep -E '^AZURE_SUBSCRIPTION_ID=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '"' || true)
      azd env new "$TARGET_AZD_ENV" \
        ${LOC:+--location "$LOC"} \
        ${SUB:+--subscription "$SUB"}
    else
      exit 1
    fi
  fi
elif ! azd env list 2>/dev/null | grep -q '(true)'; then
  echo "error: no AZURE_ENV_NAME in $ENV_FILE and no active azd env" >&2
  echo "       run 'azd env new <name>' first" >&2
  exit 1
fi

count=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"

  [[ -z "$line" || "$line" == \#* ]] && continue

  if [[ "$line" != *=* ]]; then
    echo "warn: skipping malformed line: $line" >&2
    continue
  fi

  key="${line%%=*}"
  value="${line#*=}"

  [[ -z "$value" ]] && continue

  if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
    value="${value:1:${#value}-2}"
  fi

  azd env set "$key" "$value" >/dev/null
  count=$((count + 1))
  case "$key" in
    *PASSWORD*|*SECRET*|*TOKEN*|*KEY*) echo "  set $key=***" ;;
    *) echo "  set $key=$value" ;;
  esac
done < "$ENV_FILE"

echo "Loaded $count value(s) from $ENV_FILE"
if [[ "$AZD_OUTDATED" == "1" ]]; then
  echo "note: azd is out of date — upgrade with: curl -fsSL https://aka.ms/install-azd.sh | bash" >&2
fi
