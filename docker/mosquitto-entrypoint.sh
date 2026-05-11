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

# `mosquitto_passwd` writes the file as 0600 owned by the current user
# (root in this container). The upstream entrypoint then runs the broker
# as user `mosquitto` (uid 1883), which would otherwise EACCES the read
# and crash with "password-file: Error: Unable to open pwfile". Hand
# ownership over and keep the mode tight.
chown mosquitto:mosquitto "$PASSWD_FILE"
chmod 0640 "$PASSWD_FILE"

# Sprint 28 C6 — optional TLS listener on 8883. When all three
# MOSQUITTO_TLS_* env vars are set, materialise the cert files into
# /mosquitto/config and drop a listener fragment into
# /mosquitto/config/conf.d/ (included by mosquitto.prod.conf). Missing
# any of the three = no TLS listener, broker keeps listening on 1883
# only. Cutover plan: docs/runbooks/mqtt-outage.md §"TLS cutover".
CONF_D=/mosquitto/config/conf.d
mkdir -p "$CONF_D"
if [ -n "${MOSQUITTO_TLS_CA:-}" ] \
   && [ -n "${MOSQUITTO_TLS_CERT:-}" ] \
   && [ -n "${MOSQUITTO_TLS_KEY:-}" ]; then
    CA_FILE=/mosquitto/config/ca.pem
    CERT_FILE=/mosquitto/config/server.pem
    KEY_FILE=/mosquitto/config/server.key
    printf '%s' "$MOSQUITTO_TLS_CA" > "$CA_FILE"
    printf '%s' "$MOSQUITTO_TLS_CERT" > "$CERT_FILE"
    printf '%s' "$MOSQUITTO_TLS_KEY" > "$KEY_FILE"
    chown mosquitto:mosquitto "$CA_FILE" "$CERT_FILE" "$KEY_FILE"
    chmod 0640 "$CA_FILE" "$CERT_FILE" "$KEY_FILE"
    cat > "$CONF_D/tls.conf" <<EOF
listener 8883
cafile $CA_FILE
certfile $CERT_FILE
keyfile $KEY_FILE
require_certificate false
tls_version tlsv1.2
allow_anonymous false
password_file /mosquitto/config/mosquitto.passwd
EOF
    chown mosquitto:mosquitto "$CONF_D/tls.conf"
    chmod 0640 "$CONF_D/tls.conf"
    echo "mosquitto-entrypoint: TLS listener on 8883 enabled" >&2
else
    # Make sure no stale TLS fragment from a previous boot remains.
    rm -f "$CONF_D/tls.conf"
fi

# Hand off to the upstream Mosquitto entrypoint so its signal handling and
# default arg parsing are preserved.
exec /docker-entrypoint.sh "$@"
