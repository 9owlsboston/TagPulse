#!/usr/bin/env bash
# Drive paho_smoke_publisher.py from a CSV track, one --once invocation per row.
#
# Trade-off vs the script's built-in --track flag:
#   - Pros: zero code changes, easy to splice in shell-side logic between points
#     (e.g. log to a file, fire a webhook, sleep on jitter).
#   - Cons: opens a fresh MQTT connection per point (~hundreds of ms of CONNACK
#     overhead), so realistic >0.5 Hz movement is not feasible. For that, use:
#         paho_smoke_publisher.py --track <csv> --track-interp 1
#
# Usage:
#   ./drive_track.sh tracks/boston-loop.csv [extra args passed to publisher...]
#
# CSV columns: lat,lon[,accuracy_m,dwell_s]   (header row optional)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <track.csv> [extra publisher args...]" >&2
  exit 2
fi

TRACK="$1"; shift
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PUBLISHER="${SCRIPT_DIR}/paho_smoke_publisher.py"

if [[ ! -f "$TRACK" ]]; then
  echo "track file not found: $TRACK" >&2
  exit 2
fi

while IFS=, read -r lat lon acc dwell || [[ -n "${lat:-}" ]]; do
  # Skip blank lines, comments, and a header row whose first cell isn't a float.
  [[ -z "${lat// }" ]] && continue
  [[ "${lat# }" == \#* ]] && continue
  if ! [[ "$lat" =~ ^-?[0-9]+\.?[0-9]*$ ]]; then
    continue
  fi

  args=( --once --topic location --lat "$lat" --lon "$lon" )
  [[ -n "${acc:-}" ]] && args+=( --accuracy "$acc" )

  python3 "$PUBLISHER" "${args[@]}" "$@"

  sleep "${dwell:-2}"
done < "$TRACK"
