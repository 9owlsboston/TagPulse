#!/usr/bin/env bash
# scripts/azd-ui-token.sh <env> [--print]
#
# Sprint 24 A1. Read-only helper that fetches the Static Web App
# deployment token (apiKey) for the env's SWA and prints it to stdout.
# Used by:
#   - operators wiring a GitHub Environment for the TagPulse-UI repo
#     by hand;
#   - the UI repo's `scripts/ui-bootstrap.sh` which generates
#     `.env.<env>` from this repo's azd env values.
#
# The token IS a secret (anyone with it can deploy arbitrary code into
# the production SWA), so by default we refuse to print it to a TTY —
# pass --print to override (intended for piping into `gh secret set`
# or interactive copy/paste, not for screenshots in the team chat).
#
# Idempotent. Read-only. Does not mutate Azure state.
#
# Exit codes:
#   0 — token printed to stdout
#   1 — usage error / env not found / az not signed in
#   2 — refusing to print to a TTY without --print
#
# Usage:
#   scripts/azd-ui-token.sh dev               # safe-by-default; refuses TTY
#   scripts/azd-ui-token.sh dev --print       # explicit override
#   scripts/azd-ui-token.sh dev | gh -R 9owlsboston/TagPulse-UI \
#       secret set AZURE_STATIC_WEB_APPS_API_TOKEN --env dev

set -euo pipefail

ENV_NAME=""
ALLOW_TTY=0
for arg in "$@"; do
  case "$arg" in
    --print) ALLOW_TTY=1 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
    *)
      if [[ -z "$ENV_NAME" ]]; then
        ENV_NAME="$arg"
      else
        echo "Unexpected positional arg: $arg" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$ENV_NAME" ]]; then
  echo "Usage: $0 <env> [--print]" >&2
  exit 1
fi

if ! command -v az >/dev/null 2>&1; then
  echo "error: az CLI not found on PATH" >&2
  exit 1
fi

if ! command -v azd >/dev/null 2>&1; then
  echo "error: azd CLI not found on PATH" >&2
  exit 1
fi

# Refuse to print to an interactive terminal unless explicitly asked,
# so the token doesn't leak into bash history or terminal scrollback by
# accident. Piping into gh / xclip / a file is fine.
if [[ -t 1 && "$ALLOW_TTY" -eq 0 ]]; then
  cat >&2 <<EOF
error: refusing to print SWA deployment token to a TTY.

The token is a write-credential for the production SWA. Either pipe it
into a tool that consumes it (recommended), or pass --print if you
really want it on screen:

    $0 $ENV_NAME | gh -R 9owlsboston/TagPulse-UI secret set \\
        AZURE_STATIC_WEB_APPS_API_TOKEN --env $ENV_NAME

    $0 $ENV_NAME --print
EOF
  exit 2
fi

# Use the existing get() helper pattern from azd-mqtt-build.sh —
# `azd env get-value` writes its "key not found" error to STDOUT and
# exits non-zero, so we have to capture and discard the error.
get() {
  local v
  if v=$(azd -e "tagpulse-${ENV_NAME}" env get-value "$1" 2>/dev/null); then
    printf '%s' "$v" | tr -d '\r'
  fi
}

SWA_NAME="$(get AZURE_STATIC_WEB_APPS_NAME)"
RG_NAME="$(get AZURE_RESOURCE_GROUP)"

if [[ -z "$SWA_NAME" ]]; then
  cat >&2 <<EOF
error: AZURE_STATIC_WEB_APPS_NAME not set in azd env tagpulse-${ENV_NAME}.

Run 'azd up' (or at least 'azd provision') against env '${ENV_NAME}'
first so the SWA exists and the env values are populated.
EOF
  exit 1
fi

if [[ -z "$RG_NAME" ]]; then
  echo "error: AZURE_RESOURCE_GROUP not set in azd env tagpulse-${ENV_NAME}." >&2
  exit 1
fi

# `secrets list` is the documented, read-only way to retrieve the
# deployment token (does not rotate it; that's `secrets reset-api-key`).
TOKEN="$(az staticwebapp secrets list \
  --name "$SWA_NAME" \
  --resource-group "$RG_NAME" \
  --query 'properties.apiKey' \
  -o tsv 2>/dev/null || true)"

if [[ -z "$TOKEN" ]]; then
  cat >&2 <<EOF
error: failed to retrieve SWA apiKey for ${SWA_NAME} in ${RG_NAME}.

Check that you're signed in to the right tenant:
    az account show
And that the SWA exists:
    az staticwebapp show --name ${SWA_NAME} --resource-group ${RG_NAME}
EOF
  exit 1
fi

printf '%s\n' "$TOKEN"
