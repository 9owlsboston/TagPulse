# ADR-011: Device Identity Roadmap

**Status:** proposed
**Date:** 2026-05-02
**Related:** [ADR-002](002-mqtt-device-connectivity.md), [Sprint 12 design](../design/identity-device-provisioning.md), [Sprint 16 design](../design/edge-device-contract.md)

## Context

TagPulse devices today authenticate with a long-lived per-device token issued at provisioning. The token is generated once, stored hashed (SHA-256, prefix-indexed), and never rotated unless an admin manually deletes and re-creates the device. There is:

- **No rotation** — a stolen token is valid until manual revocation.
- **No cryptographic device identity** — the token is a bearer secret; the device proves nothing about *which physical edge device* it is.
- **No path to mTLS** — broker is currently configured for username/password auth; cert PKI is not stood up.

As fleets grow past hand-managed scale (~hundreds → thousands of devices), the operational and security gaps compound. We need a roadmap that delivers immediate revocation value without prematurely committing to PKI tooling we may not need.

## Decision

Adopt a **three-phase device identity roadmap**. Each phase is independently shippable and reversible.

### Phase 1 — Rotatable per-device tokens (Sprint 16, this commit)

- Add `POST /device-registry/{id}/rotate-token` (admin only).
- Token generation uses the same SHA-256 hashing pipeline as user API keys ([identity-device-provisioning.md](../design/identity-device-provisioning.md)).
- Atomic rotation: new hash stored, old hash invalidated, audit row written.
- Plaintext token returned **once** in the response; never re-readable.
- UI: token rotation button + copy-once modal on device detail (admin only).
- No grace period — devices must support live token reload (the `clients/pi/` reference client does).

**Why first:** small surface area, immediate revocation, no broker work, no PKI. Solves the "stolen token" worst case in one sprint.

### Phase 2 — mTLS for MQTT (Sprint 17b, separate ADR-012)

- Per-device X.509 client cert issued at provisioning (or first rotation).
- Cert thumbprint stored on `devices`; broker enforces mTLS; `device_id` derived from cert subject.
- Backend retains Phase-1 token path for HTTP and legacy devices (backward compatible).
- Broker selection (Mosquitto vs EMQX) gated by mTLS support and operability — captured in ADR-012.

**Why second:** mTLS is the production target but requires PKI tooling, broker config, and a CA decision. Phase 1 delivers most of the security win sooner; Phase 2 closes the bearer-token weakness for MQTT specifically.

### Phase 3 — TPM/DICE-backed keys (no timeline)

- Hardware-backed keys (TPM / DICE / Secure Element) where the platform supports it — Pi 4/5+ via fTPM, industrial gateways via discrete TPM 2.0, embedded SoCs via SE. Private key never leaves the device.
- Provisioning uses a hardware-backed CSR; backend pins the device to its hardware-attested public key.
- Optional and customer-driven; pure-software identity (Phases 1–2) is acceptable for most deployments.

**Why deferred:** customer requirement signal is weak today. Documented so the design above does not foreclose it.

## Consequences

**Positive**

- Phase 1 ships in Sprint 16 with no broker dependency.
- Phase 2 has a clear handoff (cert thumbprint column, broker change) without rework.
- Each phase is reversible: feature flag + DB column rollback.
- Audit trail covers all identity changes from day one.

**Negative**

- Phase 1 requires devices to support live token reload. The reference edge client (`clients/pi/`) does; third-party clients must implement the same. The wire contract ([Sprint 16 design](../design/edge-device-contract.md) §3.10) makes this a conformance requirement.
- Two coexisting auth paths (token + mTLS) during the Phase 2 rollout window. Mitigated by routing logic that prefers mTLS when present.
- TPM phase is documented but not designed; if it becomes urgent, expect a 1-sprint design spike.

**Neutral**

- No change to user authentication (JWT + API keys). Device identity and user identity remain orthogonal systems.

## Alternatives Considered

1. **Skip Phase 1, jump to mTLS.** Faster to "done state" but blocks on broker decision and PKI tooling. Leaves the rotation gap open for another full sprint cycle.
2. **OAuth / JWT for devices.** Reuses user-auth infrastructure but adds key-rotation and refresh complexity that bearer tokens already solve simply. Doesn't materially improve the impersonation story over Phase 1.
3. **Pre-shared certs without rotation.** Worst of both worlds — PKI complexity without the revocation benefits.

## Validation

Phase 1 is considered done when:

- Token rotation API works end-to-end with admin auth.
- Old token is rejected immediately after rotation (integration test).
- Audit log entry exists for every rotation.
- UI surfaces rotation with a copy-once flow.
- Reference edge client recovers from a 401 by reloading its token without restarting (unit test).

Phase 2 acceptance criteria deferred to ADR-012.
