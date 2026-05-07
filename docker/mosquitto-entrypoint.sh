#!/bin/sh
# docker/mosquitto-entrypoint.sh — Sprint 23 Phase A.
#
# Materialise /mosquitto/config/mosquitto.passwd from the MOSQUITTO_USERNAME
# and MOSQUITTO_PASSWORD env vars before starting the broker. Replaces the
# previous Azure Files seeding step (scripts/azd-bootstrap-mqtt.sh) and the
# corporate-policy-blocked SMB volume mount.
#
# Fails fast if either env var is empty so a misconfigured deploy doesn't
# silently boot an open broker.

set -eu

if [ -z "${MOSQUITTO_USERNAME:-}" ] || [ -z "${MOSQUITTO_PASSWORD:-}" ]; then
    echo "mosquitto-entrypoint: MOSQUITTO_USERNAME and MOSQUITTO_PASSWORD must be set" >&2
    exit 1
fi

PASSWD_FILE=/mosquitto/config/mosquitto.passwd

# `mosquitto_passwd -b -c` overwrites; safe on every boot. The file ends up
# inside the image's writable layer, which is fine — it's regenerated each
# start from the env vars (sourced from Key Vault in cloud).
mosquitto_passwd -b -c "$PASSWD_FILE" "$MOSQUITTO_USERNAME" "$MOSQUITTO_PASSWORD" >/dev/null

# Hand off to the upstream Mosquitto entrypoint so its signal handling and
# default arg parsing are preserved.
exec /docker-entrypoint.sh "$@"
