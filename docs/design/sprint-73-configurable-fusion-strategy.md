# Sprint 73 — Configurable fusion strategy (Tenant Settings)

- Status: **In progress** (2026-06-21). Backend [#147](https://github.com/9owlsboston/TagPulse/pull/147) + UI [#110](https://github.com/9owlsboston/TagPulse-UI/pull/110).
- Owner: tenant-config contract + admin UI. Cross-repo.
- Related: [ADR-034 (asset state consolidation)](../adr/034-asset-state-consolidation.md),
  `tenants.fusion_strategy` (migration 058), `scripts/set_fusion_strategy.py`,
  `TenantSettings.tsx` (TagPulse-UI).

## 1. Why

Sprints 71–72 added the per-tenant **`fusion_strategy`** config (decay τ,
cadence, look-back, RSSI floor, min-reads, cold-chain SLA) but left it
**unreachable from the App** — it was set only out-of-band via the
`set_fusion_strategy.py` ops script (DB-direct tools-job). An operator looking
for the **decay control** (or the SLA band that powers the Journey chart) found
nothing in Tenant Settings. This sprint closes that gap.

## 2. Decisions

- **Expose `fusion_strategy` on the tenant-config contract** (`GET`/`PATCH
  /tenant/config`) as the typed `FusionStrategy` model (incl. `sla`).
- **PATCH semantics via `model_fields_set`** (presence), not `is not None`, so
  the admin UI can both **set** (object) and **clear** (explicit `null` → opt
  out) — `null` ≠ omitted. This differs from the other config fields (which use
  `is not None`) precisely because a `fusion_strategy` of `null` is meaningful.
- **Admin-only** (the PATCH already requires `admin`; the page is admin-reached).
- **Harden the ops script** to **merge** (read-modify-write) instead of replace,
  so a partial `--set` no longer clobbers untouched knobs (the SLA footgun).

## 3. Surfaces

- **Backend** ([tenant_config.py](../../src/tagpulse/api/routes/tenant_config.py)):
  `TenantConfig.fusion_strategy` (GET, parsed from the JSONB) +
  `TenantConfigUpdate.fusion_strategy` (PATCH, validated by `FusionStrategy`).
  `openapi.json` regenerated.
- **Script** ([set_fusion_strategy.py](../../scripts/set_fusion_strategy.py)):
  knob defaults → `None` sentinels; `_load` + `_merge` apply only provided flags.
- **UI**: a **"Consolidation"** tab in Tenant Settings — Enable toggle (off →
  `fusion_strategy: null`), the decay/cadence/look-back/RSSI/min-reads knobs, and
  a **Cold-chain SLA** card (enable + temp/humidity envelope + excursion
  tolerance). Wired to `PATCH /tenant/config`.

## 4. Out of scope

`position_strategy` (the floor-positioning sibling) stays ops-script-only for now
— same generalization could follow if there's demand. No new tenant column (reuses
`fusion_strategy` JSONB from migration 058).
