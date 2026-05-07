# ADR-012: mTLS for MQTT (broker selection + PKI tooling)

- Status: accepted
- Date: 2026-05-02
- Supersedes: none
- Related: [ADR-002 MQTT for device connectivity](002-mqtt-device-connectivity.md), [ADR-011 Device identity roadmap](011-device-identity-roadmap.md), [docs/design/edge-device-contract.md](../design/edge-device-contract.md)

## Context

Sprint 16 (ADR-011 Phase 1) shipped per-device rotatable Bearer tokens. That gives us cryptographic device identity over HTTPS but the MQTT broker still trusts a single shared username/password per environment — a credential that, once leaked from a single field reader, lets an attacker publish on any tag-read topic for any tenant.

Phase 2 of ADR-011 was always going to be mTLS for MQTT. Sprint 17b commits to it: every reader presents a client certificate; the broker validates against a tenant CA; the backend maps the certificate's SHA-256 thumbprint to a `devices` row and stamps `tenant_id` on every ingested event from that connection.

## Decision

**Broker:** Eclipse Mosquitto 2.x with TLS + `auth_plugin`-driven thumbprint lookup (the `mosquitto-go-auth` HTTP backend pointing at TagPulse's `/internal/mqtt-auth` endpoint).

We considered EMQX. EMQX has nicer dashboards, better clustering, and a built-in PostgreSQL ACL backend, but for a single-broker MVP its operational footprint (Erlang VM, multi-process supervision, license drift between OSS and Enterprise) is overkill versus what Mosquitto + a small Python authn shim can do. EMQX stays on the table for ADR-014 if/when we need clustering or shared subscriptions; the broker abstraction in `tagpulse.ingestion.mqtt` keeps both options open.

**PKI tooling:** [smallstep `step-ca`](https://smallstep.com/docs/step-ca) running in a sidecar container. One root CA per environment; one intermediate per tenant (issued at tenant onboarding by `services.pki`). Devices receive 90-day leaf certs minted via the existing provisioning flow (the provisioning JWT from ADR-011 Phase 0 is exchanged for a CSR-signing endpoint instead of a Bearer token). Renewal is automated by the device-side agent in the Pi client.

**Database surface:** `devices.cert_thumbprint VARCHAR(128) UNIQUE` (where not null), `devices.cert_subject VARCHAR(255)`. The plaintext PEM never enters the application database — only the thumbprint and subject. Devices may carry both a token (Phase 1) and a cert (Phase 2) during the migration window; the backend prefers cert auth when present, falls back to token, and audits which credential authenticated each connection.

**Authentication endpoint:** new internal `POST /internal/mqtt-auth` consumed by `mosquitto-go-auth`. Receives `{username, client_id, cert_pem}`, returns `{tenant_id, device_id, allow_topics}`. Rate-limited per source IP, never exposed publicly.

## Backward compatibility

ADR-011 Phase 1 token devices keep working. Until a tenant's last token-only device is decommissioned the broker accepts both auth methods. The migration path:

1. Operator attaches a cert to each device via `POST /device-registry/{id}/cert` (admin-only, audit-logged, metered via `tagpulse_device_cert_attachments_total`).
2. Once all devices in a tenant carry certs, operator flips `tenants.require_mtls = true` (a future Sprint 17c flag) and the broker stops accepting token-only auth for that tenant.
3. Existing tokens remain rotatable for emergency fallback for 30 days, then expire.

## Why not WebSockets-over-HTTPS only?

Reader hardware ranges from sub-$100 ARM boards to mid-range industrial gateways. MQTT-over-TLS is cheaper on memory and battery than HTTPS-WebSocket framing for the bursty publish pattern, and it's what 90% of off-the-shelf RFID readers speak natively. Keeping MQTT means we don't force a firmware swap.

## Trade-offs accepted

- **Cert lifecycle is now operational surface.** Renewal failures bring devices offline. Mitigated by the 90-day window + dashboard alert (`devices_with_expiring_certs_total` Prometheus query) at 14 days remaining.
- **Mosquitto is a single point of failure.** Acceptable for MVP. Clustering opens [ADR-014: MQTT broker scale-out](014-mqtt-broker-scale-out.md) when we cross ~10k concurrent connections.
- **Adds `cryptography` to backend deps.** Already a transitive dep of `httpx` in many setups; explicit pin keeps the cert-parsing path (`api/routes/devices.py::attach_device_cert`) reproducible.

## Consequences

- New backend dep: `cryptography>=43.0`.
- New schema columns: `devices.cert_thumbprint`, `devices.cert_subject` (migration 026).
- New admin route: `POST /device-registry/{id}/cert`.
- New OTel counter: `tagpulse_device_cert_attachments_total`.
- New ADRs likely to follow:
  - ADR-013: PostGIS adoption (already triggered conditionally by Sprint 17a metrics).
  - ADR-014: MQTT broker scale-out (Mosquitto -> EMQX).

## Open questions deferred to follow-up sprints

- `tenants.require_mtls` enforcement flag (Sprint 17c).
- Internal `POST /internal/mqtt-auth` shim + Mosquitto `auth_plugin` config (Sprint 17c — Phase 2 only ships the cert *attachment* surface in 17b).
- step-ca sidecar Helm chart + tenant CA provisioning workflow (Sprint 17c).
