# Runbook: First Device Token Rotation

**Owner:** Platform Operations
**Audience:** Tenant admins, on-call platform engineers
**Source:** [docs/design/edge-device-contract.md §5, §10](../design/edge-device-contract.md), ADR-011

---

## When to use

- A device has been **physically lost or compromised**.
- A device's **shared key** is suspected to have leaked.
- Routine **90-day rotation** (recommended cadence; alerting added in Sprint 18).
- Onboarding a previously-shared device into per-device-token mode.

## Pre-flight (one-time per environment)

1. Confirm migration **`025_device_tokens.py`** is applied:
   ```bash
   alembic current
   # expect → 025 (head) or later
   ```
2. Confirm the rotation route is reachable:
   ```bash
   curl -sS https://<host>/openapi.json | jq '.paths | keys[] | select(test("rotate-token"))'
   # expect → "/device-registry/{device_id}/rotate-token"
   ```
3. Verify the OTel counter is exported:
   ```promql
   tagpulse_device_token_rotations_total
   ```

## Rotation procedure

> **Critical:** rotation invalidates the running token immediately — no grace
> period (per design §11 Q1). Plan a brief disconnect window and have the new
> token-delivery channel ready before clicking *Rotate*.

### From the admin UI

1. Sign in as **admin** (the rotate button is admin-only via `RoleGuard`).
2. Navigate to **Devices → \<device name> → Security**.
3. Note the current `Last rotated` timestamp and `Token prefix`.
4. Click **Rotate token** → confirm in the warning dialog.
5. The new token is shown **once** in a copy-once modal:
   - Click **Copy to clipboard** (or select-and-copy manually).
   - Save it to your secret store (Key Vault / 1Password / etc.) immediately.
   - Closing the modal discards the value — backend stores SHA-256 only.
6. Push the new token to the device out-of-band:
   - Reference client: update `clients/pi/.env` → `TAGPULSE_DEVICE_TOKEN=<new>` and `systemctl restart tagpulse-edge`.
   - Custom firmware: surface the new token via the same channel used for
     provisioning (config file, secrets manager, etc.).
7. The reference edge agent's MQTT transport surfaces a `TokenRevokedError`
   when the broker rejects credentials (reason code 135 / 134 / 5 / 4) —
   embedders may register `on_token_revoked=` to swap in the new token
   without restart (see `clients/pi/tagpulse_edge/transport.py`).

### From the API (scripted bulk ops)

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  https://<host>/device-registry/$DEVICE_ID/rotate-token
```

Response:

```json
{
  "device_id": "…",
  "token": "tpd_<tenant-slug>_<32-hex>",
  "prefix": "tpd_<slug>",
  "rotated_at": "2026-05-…Z"
}
```

The plaintext `token` value is returned **once**. Persist it before reading
the next response.

## Verification

1. The audit log entry lands within ~1s:
   ```sql
   SELECT created_at, action, changes
     FROM audit_logs
    WHERE resource_id = '<device-id>'
    ORDER BY created_at DESC LIMIT 1;
   -- expect → action='device.token_rotated',
   --         changes={'prior_prefix': '…', 'new_prefix': '…'}
   ```
2. The Prometheus counter increments:
   ```promql
   increase(tagpulse_device_token_rotations_total[5m]) > 0
   ```
3. The device's first publish under the new token updates `devices.last_seen`.

## Rollback

There is **no rollback** — the prior token is immediately revoked. If the
device cannot accept the new token:

1. **Re-rotate** (issues a fresh token); deliver again via the same channel.
2. If rotation itself was a mistake (wrong device targeted), the device must
   be re-provisioned end-to-end. The original token cannot be recovered.

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `401` from the API | Token never reached the device, or device cached the old one | Verify the device-side credential file; restart the edge agent. |
| Device flapping between `online` and `offline` after rotation | Two devices sharing the rotated `device_id` | Decommission the duplicate; rotate again. |
| Audit row missing | Backend write transaction rolled back | Check API logs for `AuditLogger` warnings; replay rotation. |
| Counter unchanged | OTel exporter restart in progress | Wait one scrape interval (default 15s); confirm via `/metrics`. |

## Related

- [docs/design/edge-device-contract.md §5](../design/edge-device-contract.md) — token rotation API
- [docs/design/edge-device-contract.md §8](../design/edge-device-contract.md) — `TokenRevokedError` on the edge client
- [docs/adr/011-device-identity-roadmap.md](../adr/011-device-identity-roadmap.md)
- [docs/design/identity-device-provisioning.md](../design/identity-device-provisioning.md)
