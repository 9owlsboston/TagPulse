#!/usr/bin/env bash
# scripts/azd-env-load.sh
#
# Load a local .env file into the active azd environment via `azd env set`.
# Usage:
#     azd env new tagpulse-prod        # one-time
#     scripts/azd-env-load.sh deploy/azure/.env
#     azd up
#
# Lines that are blank, start with '#', or have an empty value are skipped.
# Quotes around values are stripped. No expansion / interpolation is done.

set -euo pipefail

ENV_FILE="${1:-deploy/azure/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found" >&2
  echo "       cp deploy/azure/.env.example $ENV_FILE  # then edit" >&2
  exit 1
fi

if ! command -v azd >/dev/null 2>&1; then
  echo "error: azd not on PATH (install: https://aka.ms/install-azd)" >&2
  exit 1
fi

if ! azd env list 2>/dev/null | grep -q '(true)'; then
  echo "error: no active azd environment — run 'azd env new <name>' first" >&2
  exit 1
fi

count=0
while IFS= read -r line || [[ -n "$line" ]]; do
  # Strip leading/trailing whitespace
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"

  # Skip blanks + comments
  [[ -z "$line" || "$line" == \#* ]] && continue

  # Must look like KEY=VALUE
  if [[ "$line" != *=* ]]; then
    echo "warn: skipping malformed line: $line" >&2
    continue
  fi

  key="${line%%=*}"
  value="${line#*=}"

  # Skip empty values (postprovision-populated keys live in the example)
  [[ -z "$value" ]] && continue

  # Strip surrounding quotes
  if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
    value="${value:1:${#value}-2}"
  fi

  azd env set "$key" "$value" >/dev/null
  count=$((count + 1))
  # Mask secret-shaped values in stdout
  case "$key" in
    *PASSWORD*|*SECRET*|*TOKEN*|*KEY*) echo "  set $key=***" ;;
    *) echo "  set $key=$value" ;;
  esac
done < "$ENV_FILE"

echo "Loaded $count value(s) into azd env: $(azd env list | awk '/\(true\)/{print $1}')"
