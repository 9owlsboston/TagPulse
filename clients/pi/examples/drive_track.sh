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
#   # Drive an asset-bound device (binding_kind='device', binding_value=TAG0003).
#   # When TAG_ID is set, each waypoint is published as a tag-read whose
#   # payload embeds the lat/lon under "location". This is what the
#   # asset_current_location view + Data Explorer + Asset detail Path tab
#   # actually consume — pure /location messages only update devices.last_lat/lon
#   # and do NOT show in the explorer or update the asset path.
#   TAG_ID=TAG0003 ./drive_track.sh tracks/sfo-loop.csv
#
# CSV columns: lat,lon[,accuracy_m,dwell_s]   (header row optional)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <track.csv> [extra publisher args...]" >&2
  echo "  set TAG_ID=<tag_id> to publish tag-reads instead of /location" >&2
  exit 2
fi

TRACK="$1"; shift
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PUBLISHER="${SCRIPT_DIR}/paho_smoke_publisher.py"

if [[ ! -f "$TRACK" ]]; then
  echo "track file not found: $TRACK" >&2
  exit 2
fi

# When TAG_ID is set, publish tag-reads (default topic) carrying embedded
# location. Otherwise publish device-level /location updates.
if [[ -n "${TAG_ID:-}" ]]; then
  TOPIC_ARGS=( --topic tag-reads --tag-id "$TAG_ID" )
  echo "[drive_track] mode=tag-reads tag_id=$TAG_ID (asset/binding-aware)"
else
  TOPIC_ARGS=( --topic location )
  echo "[drive_track] mode=location (device GPS only — set TAG_ID=... for asset path)"
fi

while IFS=, read -r lat lon acc dwell || [[ -n "${lat:-}" ]]; do
  # Skip blank lines, comments, and a header row whose first cell isn't a float.
  [[ -z "${lat// }" ]] && continue
  [[ "${lat# }" == \#* ]] && continue
  if ! [[ "$lat" =~ ^-?[0-9]+\.?[0-9]*$ ]]; then
    continue
  fi

  args=( --once "${TOPIC_ARGS[@]}" --lat "$lat" --lon "$lon" )
  [[ -n "${acc:-}" ]] && args+=( --accuracy "$acc" )

  python3 "$PUBLISHER" "${args[@]}" "$@"

  sleep "${dwell:-2}"
done < "$TRACK"

