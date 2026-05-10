# Design Document: Edge Device Contract & Identity Hardening (Sprint 16)

**Date:** 2026-05-02
**Status:** proposed
**Related:** [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md) (A5, A6), [identity-device-provisioning.md](identity-device-provisioning.md), [docs/refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md) (non-normative reference hardware + non-RFID peripherals), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md) (consumer of the dedup / ENTER-EXIT contract for mobile-reader bandwidth control), ADR-011 (this sprint)

---

## 1. Problem Statement

Two related gaps:

1. **No documented wire contract.** The reference client at `clients/pi/` enforces a specific dedup, ENTER/EXIT, batching, and clock policy — but third parties writing their own firmware (whether for a Pi-class board, an industrial gateway, or an off-the-shelf scanner) have no spec to target. Different devices behave differently on the wire; the backend has no enforcement floor.
2. **Weak per-device identity.** Provisioning issues a long-lived token. A stolen edge device can impersonate its `device_id` indefinitely. There is no token rotation, no revocation audit trail beyond manual revocation, and no path to mTLS yet.

This sprint codifies the contract, promotes minimum enforcement to the backend, and lays the rotation foundation for mTLS in Sprint 17b.

---

## 2. Scope

In scope:

- Authoritative spec: `docs/design/edge-device-contract.md` (delivered with this design as §3 of the contract section).
- Backend ingestion middleware enforcing **clock window** + **payload size limits** for every device-originated event.
- Token rotation API + audit + UI (admin only).
- ADR-011 — device identity roadmap (Phase 1 token rotation → Phase 2 mTLS → Phase 3 TPM).
- UI parity (security panel, heartbeat panel, audit log preset).
- Edge client documentation linking back to the contract.

Out of scope:

- mTLS itself (Sprint 17b).
- Cloud-to-device commands (backlog G8).
- TPM-backed keys (Phase 3, no timeline).

---

## 3. The Contract (authoritative)

The full text below is what ships as `docs/design/edge-device-contract.md`. It is the on-the-wire spec every TagPulse-compatible device must satisfy.

### 3.1 Identity

- Each device has a unique `device_id` (UUID, server-issued at provisioning).
- Each device holds exactly one **active token**. Provisioning, approval, and rotation each issue a new token and revoke the previous.
- Token is presented as `Authorization: Bearer <token>` on HTTP and as MQTT username/password (`device_id` / `token`).

### 3.2 Topic taxonomy

```
tenants/{tenant_id}/devices/{device_id}/tag-reads
tenants/{tenant_id}/devices/{device_id}/status
tenants/{tenant_id}/devices/{device_id}/telemetry
tenants/{tenant_id}/devices/{device_id}/location
tenants/{tenant_id}/devices/{device_id}/events
```

Devices may publish only on their own `device_id`. The broker rejects writes elsewhere (ACL).

### 3.3 Dedup and ENTER/EXIT (RFID readers)

- Suppress identical `(tag_id, reader_antenna)` reads within `dedup_window_s` (default **5 s**).
- Publish one `tag_read` event with `event_type='enter'` when a tag first appears.
- Publish one `tag_read` event with `event_type='exit'` when a tag has been absent for `exit_timeout_s` (default **10 s**).
- `event_type ∈ {'enter','exit','present','absent'}`. `'present'`/`'absent'` are diagnostic only.

### 3.4 Batching

- HTTP: up to 100 readings per `POST /tag-reads` or `/telemetry`.
- MQTT: each publish carries up to 100 events, flushed every ≤1 s under load.
- A single MQTT message must be ≤256 KB after JSON encoding (server limit; broker limit is higher to absorb spikes).

### 3.4.1 Tag-read payload shape (MQTT)

The `…/tag-reads` topic accepts either a single reading object or an
array of up to 100 reading objects. `device_id` is **derived from the
topic** (`tenants/{tid}/devices/{did}/tag-reads`); if the body carries
its own `device_id` field the broker silently drops it in favour of
the topic-derived UUID. This guarantees a misrouted publisher cannot
ingest reads under another device, and matches the smoke publisher in
`clients/pi/examples/paho_smoke_publisher.py`.

Conformance: a malformed publish (non-JSON bytes, JSON scalar, schema
mismatch) MUST be logged + dropped at the subscriber, not propagate to
the message loop. See [Sprint 31 issue #18](https://github.com/9owlsboston/TagPulse/issues/18) for the regression that motivated this rule.

### 3.5 Clock rules

- All `timestamp` fields **MUST** be UTC ISO-8601 with `Z` suffix or explicit `+00:00`.
- Devices **MUST** sync NTP on boot and at least every 6 h.
- The backend **rejects** events with:
  - `timestamp < now − 24 h` → reason `event_too_old`
  - `timestamp > now + 5 min` → reason `event_in_future`
- Rejection is logged + dead-lettered + metered (`events_rejected_clock`); no 5xx returned.

### 3.6 Heartbeat and status

- Devices publish to `…/status` every **60 s** (`heartbeat_interval_s`):

```json
{
  "timestamp": "…",
  "connection_state": "online",
  "firmware_version": "0.4.2",
  "uptime_s": 12345,
  "queue_depth": 0,
  "buffer_bytes": 0
}
```

- MQTT Last Will and Testament publishes `{"connection_state":"offline"}` to the same topic on ungraceful disconnect.

### 3.7 Offline buffer

- Devices buffer to local persistent storage on transport failure.
- Buffer is **size + age bounded** (default 100 MB, 24 h). Oldest events evicted first; eviction is an event on `…/events` with `event_type='buffer_evicted'`.
- On reconnect, drain in FIFO order; respect the clock window in §3.5 (older events drop on the floor with a `buffer_drained` summary event).

### 3.8 Reconnect

- Exponential backoff with **full jitter**, base 1 s, max 60 s.
- Devices publish `{"event_type":"reconnect_succeeded","attempts":N}` on `…/events` after recovery.

### 3.9 Configuration

The following knobs are device-side and **must** be readable from the device's `configuration` JSON (delivered today via provisioning; Sprint 17b+ via cloud-to-device):

| Key | Default | Notes |
|---|---|---|
| `dedup_window_s` | 5 | §3.3 |
| `exit_timeout_s` | 10 | §3.3 |
| `heartbeat_interval_s` | 60 | §3.6 |
| `batch_size_max` | 100 | §3.4 |
| `batch_flush_ms` | 1000 | §3.4 |
| `buffer_max_bytes` | 100_000_000 | §3.7 |
| `buffer_max_age_s` | 86_400 | §3.7 |
| `reconnect_base_ms` | 1000 | §3.8 |
| `reconnect_max_ms` | 60_000 | §3.8 |

The reference implementation in `clients/pi/tagpulse_edge/config.py` is the canonical source of defaults.

### 3.10 Conformance

A device is **TagPulse-compatible** when it passes:

- `tests/conformance/clock.py` — clock rules round-trip.
- `tests/conformance/dedup.py` — dedup + ENTER/EXIT semantics.
- `tests/conformance/buffer.py` — offline drain within budget.
- `tests/conformance/heartbeat.py` — LWT + heartbeat cadence.

Each test is published in this repo and runs against any device exposing a test harness on localhost. The reference client at `clients/pi/` (currently developed against Raspberry Pi-class hardware but hardware-agnostic) is the first "blessed" implementation.

---

## 4. Backend Enforcement Changes

This sprint promotes **clock window** enforcement (§3.5) from "best practices" to a hard middleware:

```python
# src/tagpulse/ingestion/middleware/clock.py  (new)
async def enforce_clock_window(event: IngestEvent) -> IngestEvent | None:
    skew = event.timestamp - now_utc()
    if skew < -timedelta(hours=24):
        await dead_letter(event, reason="event_too_old")
        meter("events_rejected_clock", tenant_id=event.tenant_id)
        return None
    if skew > timedelta(minutes=5):
        await dead_letter(event, reason="event_in_future")
        meter("events_rejected_clock", tenant_id=event.tenant_id)
        return None
    return event
```

Wired into both HTTP and MQTT ingestion paths before persistence. Dead-letter rows reuse the existing `dead_letter_events` table (Sprint 10).

Payload size limits already exist in FastAPI; we make them explicit via `MAX_INGEST_PAYLOAD_BYTES=262_144` config and document.

---

## 5. Token Rotation (A6 Phase 1)

### 5.1 API

```
POST /device-registry/{device_id}/rotate-token
  → 200 { "token": "<new-token>", "expires_at": null }
```

- Admin only (`require_role("admin")`).
- Generates new SHA-256-hashed token (same generator as user API keys; see [identity-device-provisioning.md](identity-device-provisioning.md)).
- Atomically:
  1. Hashes new token, stores `(prefix, hash)` on `devices`.
  2. Revokes the previous hash (no grace period — old token immediately invalid).
  3. Writes `audit_logs` row with `event_type='device.token_rotated'`, `actor_user_id`, `device_id`, `prior_prefix`.
- Response includes the **plaintext token exactly once**; backend never stores it again.

### 5.2 Schema change

```sql
ALTER TABLE devices
  ADD COLUMN token_hash       VARCHAR(255) NULL,
  ADD COLUMN token_prefix     VARCHAR(10)  NULL,
  ADD COLUMN token_rotated_at TIMESTAMPTZ  NULL;
```

Migration 018; existing devices keep their current shared-key auth path until rotated. Backend prefers `token_hash` when set, falls back to existing key path.

### 5.3 Metering and audit

- Dimension `device_token_rotations` per tenant.
- Audit log entry per rotation (admin-readable via existing audit API + new "device security events" filter preset).

### 5.4 Operational guidance

- Rotation invalidates the running connection. Devices must reconnect with the new token (delivered out-of-band via `clients/pi/` config update or future C2D).
- Recommended rotation cadence: every 90 d (alert generated by analytics module if exceeded — out of scope for this sprint, tracked as Sprint 18 candidate).

---

## 6. ADR-011 — Device Identity Roadmap (excerpt)

The full ADR ships as `docs/adr/011-device-identity-roadmap.md`. Decision summary:

- **Phase 1 (Sprint 16, this sprint):** rotatable per-device tokens; admin-controlled; audit-logged.
- **Phase 2 (Sprint 17b):** mTLS for MQTT; cert thumbprint stored on `devices`; broker enforces. Backward-compatible token path retained for HTTP and legacy devices.
- **Phase 3 (no timeline):** hardware-backed keys (TPM / DICE / Secure Element) for a hardware root of trust on devices that support it (e.g., Pi 4/5+ via fTPM, industrial gateways via discrete TPM 2.0).

Rationale: token rotation is a small, low-risk delta with immediate revocation value. mTLS requires broker selection + PKI tooling and is a separate ADR (012).

---

## 7. UI Parity

| Page | Change |
|---|---|
| Device detail | New **Security** panel — token last rotated, "Rotate token" button (admin), copy-once token reveal modal with download button |
| Device detail | New **Heartbeat** panel — uptime, queue depth, firmware version, connection state, LWT indicator |
| Audit log | New filter preset **Device security events** (event_type IN device.token_rotated, device.approved, device.revoked, device.cert_rotated) |
| Devices list | Optional column "Last rotated" (admin) |
| Sidebar | No structural change |

UI parity is a **release gate**.

---

## 8. Edge Client Updates

- `clients/pi/README.md` — link prominently to `docs/design/edge-device-contract.md` as the spec the client implements.
- `tagpulse_edge/transport.py` — handle 401 on publish by surfacing a `TokenRevokedError` so embedders can swap in the new token without restart.
- New unit test: token-rotation simulation drives the transport through revoke → reload-token → reconnect.

---

## 9. Testing Strategy

- Unit: clock middleware (under, in-range, over, exact boundaries).
- Unit: token rotation atomicity (old token invalid immediately, audit row written, prefix updated).
- Unit: rotate-token route requires admin; viewer/editor get 403.
- Integration: HTTP + MQTT events with bad timestamps land in dead_letter_events.
- Integration: rotated device fails auth with old token, succeeds with new.
- Conformance suite: stub harness that runs the four test files against a running edge agent.

---

## 10. Rollout

1. Migration 018 (additive token columns).
2. Deploy backend with clock middleware in **observe mode** (log + meter, do not reject) for 48 h.
3. Switch to **enforce mode**.
4. Deploy UI.
5. Document operator runbook for first rotation.

Rollback: middleware feature flag back to observe; token rotation column harmless if unused.

---

## 11. Decisions (resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Grace period on token rotation? | **No.** Devices must support live reload of credentials; the reference edge client at `clients/pi/` does. Avoids the "two valid secrets at once" anti-pattern. |
| 2 | Self-rotation by device? | **Defer.** Admin-triggered rotation only — safer trust boundary. Revisit if a self-rotation use case emerges (e.g., long-lived disconnected ops). |
| 3 | Bulk rotation across a tenant? | **Sprint 18 candidate.** Useful for incident response; not blocking. |
| 4 | Conformance harness location? | **In-repo** at `tests/conformance/`. Split into a separate `tagpulse-conformance` package only if it grows enough to need an independent release cadence. |
