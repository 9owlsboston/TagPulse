# docker/mosquitto.Dockerfile — Sprint 23 Phase A.
#
# Custom Mosquitto image baked with the production conf + an entrypoint that
# materialises the password file from MOSQUITTO_USERNAME / MOSQUITTO_PASSWORD
# env vars at boot. Replaces the corporate-policy-blocked Azure Files mount
# pattern (Sprint 22 used eclipse-mosquitto:2 with two Azure Files volumes).
#
# Built + pushed by `azd deploy mqtt` to ACR repo tagpulse-mqtt; consumed by
# the ACI in deploy/azure/bicep/modules/mqtt.bicep via UAMI ACR pull.

FROM eclipse-mosquitto:2

# Bake the hardened conf in /mosquitto/config/. Image already declares
# /mosquitto/config and /mosquitto/data as VOLUMEs upstream; we don't mount
# anything over them in cloud — the conf is in the image, the password file
# is generated at boot, and persistence is best-effort container-local.
COPY docker/mosquitto.prod.conf /mosquitto/config/mosquitto.conf
COPY docker/mosquitto-entrypoint.sh /usr/local/bin/tagpulse-mqtt-entrypoint.sh

# Alpine base — chmod is enough; no chown needed (mosquitto runs as user
# `mosquitto` which already owns /mosquitto/config).
RUN chmod +x /usr/local/bin/tagpulse-mqtt-entrypoint.sh

EXPOSE 1883
# Port 8883 (TLS) is intentionally not exposed — mTLS is the ADR-012
# workstream and ships separately from Sprint 23.

ENTRYPOINT ["/usr/local/bin/tagpulse-mqtt-entrypoint.sh"]
CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
