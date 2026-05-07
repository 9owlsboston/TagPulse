# Runbook: Subject-Scoped Telemetry — Rules & UI Operations

**Applies to:** Sprint 20 onward.
**Sources:** [ADR-013](../adr/013-telemetry-subject-scoping.md) (schema), [ADR-014](../adr/014-telemetry-multi-subject-ingest.md) (ingest+API), [ADR-015](../adr/015-telemetry-rules-and-deprecation.md) (rules engine), [docs/design/subject-scoped-telemetry.md](../design/subject-scoped-telemetry.md).

This runbook covers three operational tasks introduced by Sprint 20:

1. Enable per-tenant subject-scoped telemetry.
2. Author a `telemetry.threshold` rule (manual or via the
   `lot.cold_chain_breach` template).
3. Run the Sprint 18 → 20 deprecation sunset checklist before dropping
   the `device_telemetry` view + legacy hypertable (gated on retention
   having cycled).

---

## 1. Enable subject-scoped telemetry for a tenant

Subject-scoped telemetry is **off by default**. Without explicit opt-in,
every tag-borne metric still lands in `telemetry_readings` with
`subject_kind='device'` (Sprint 14 contract — unchanged). Opt-in turns
on the fan-out into asset / lot / stock_item rows.

### Pick the kinds the tenant needs

| If the tenant has...                                    | Add to opt-in |
|---------------------------------------------------------|---------------|
| Asset-level temperature / battery dashboards            | `asset`       |
| Lot-level cold-chain dashboards / `lot.cold_chain_breach` | `lot`       |
| Per-pallet / per-case temperature lineage               | `stock_item`  |
| Zone-level rollups (Sprint 21+)                         | `zone`        |

### Apply via the admin API

```bash
curl -X PATCH https://api.example.com/tenant/config \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"telemetry_subject_kinds": ["device", "asset", "lot"]}'
```

The change takes effect on the **next** tag-read after the
`SUBJECT_KINDS_CACHE` TTL (30 s). The writing worker is invalidated
immediately by `PATCH /tenant/config` (Sprint 21 — see
[ADR-015 §5](../adr/015-telemetry-rules-and-deprecation.md)); sibling
workers converge within one TTL. Restart workers only if operators
need faster than 30 s convergence.

### Verify fan-out is happening

After ~1 minute of ingest:

```sql
SELECT subject_kind, count(*) AS rows_last_5min
FROM telemetry_readings
WHERE tenant_id = '<tenant-uuid>'
  AND timestamp > now() - interval '5 minutes'
GROUP BY subject_kind
ORDER BY 1;
```

You should see at least one row per opted-in kind. If only `device`
appears, check application logs for `telemetry.subject_unresolved` —
the most common cause is missing `asset_tag_bindings` for the EPCs in
play.

---

## 2. Author a `telemetry.threshold` rule

### Option A — use the built-in template

```bash
# 1. Discover available templates
curl -H "Authorization: Bearer $KEY" https://api.example.com/rule-templates

# 2. Pull the cold-chain breach template
curl -H "Authorization: Bearer $KEY" \
  https://api.example.com/rule-templates/lot.cold_chain_breach
```

The response is a fully POST-able rule body. Edit `value` (default 8°C)
and `cooldown_s` (default 900s) to taste, then:

```bash
curl -X POST https://api.example.com/rules \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d @template-edited.json
```

### Option B — author by hand

```jsonc
{
  "name": "Pharma cold-chain (refrigerated)",
  "condition_type": "telemetry.threshold",
  "condition_config": {
    "subject_kind": "lot",      // or "asset" / "stock_item"
    "metric_name": "temperature_c",
    "operator": "gt",
    "value": 8.0,
    "cooldown_s": 900,
    "subject_id": null           // null = any subject of that kind
  },
  "action_type": "notification",
  "action_config": {}
}
```

Pin `subject_id` only when you want a per-instance rule (e.g. a single
high-value asset). Per-tenant cooldown is keyed on
`(tenant_id, rule_id, subject_id)` so leaving `subject_id=null` does
**not** suppress alerts across distinct lots — each subject gets its
own cooldown window.

### Verify the rule fires

Run the simulator:

```bash
python scripts/simulate_devices.py \
  --tenant-id <tenant-uuid> \
  --api-key $ADMIN_KEY \
  --cold-chain --cold-chain-period 5
```

The simulator drifts a synthetic milk lot's temperature from 4°C upward
at ~0.05°C/cycle. With `cold-chain-period=5` and threshold=8°C, you
should see a `lot.cold_chain_breach` alert within ~7-8 minutes. Inspect
via `GET /alerts?status=firing`.

---

## 3. Sprint 18 → 21 deprecation sunset checklist

> **Status (Sprint 21 — May 2026):** the sunset code shipped — see
> [migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py),
> CHANGELOG `## Unreleased`. The preconditions below are still
> mandatory before applying migration 032 in production: the gate is
> the slowest tenant's `telemetry_retention_days` cycling past the
> Sprint 18 cutover ([ADR-015 §6](../adr/015-telemetry-rules-and-deprecation.md)).

**Do not run any of this until the tenant's longest telemetry retention
window has cycled past the Sprint 18 cutover.** If you cut a tenant
over to subject-scoped telemetry on day D, wait at least D + retention
days before dropping the back-compat path. Otherwise dashboards that
joined the legacy `device_telemetry` view will silently lose history.

### Prerequisites — verify zero readers

```sql
-- 1. Anyone reading the back-compat view?
SELECT relname, idx_scan, seq_scan
FROM pg_stat_user_tables
WHERE relname IN ('telemetry_readings_legacy_device');

SELECT n_tup_ins, n_tup_upd, n_tup_del
FROM pg_stat_user_tables
WHERE relname = 'telemetry_readings_legacy_device';
```

`idx_scan + seq_scan` should be flat for at least one full retention
window. Any new ingest writes to the legacy table itself indicate a
stray subscriber writing through the old `TimescaleTelemetryRepository`
path — find and fix before proceeding.

### Inventory

Before dropping anything:

- [ ] Grafana dashboards: search every dashboard JSON for
  `device_telemetry` and `telemetry_readings_legacy_device`. Repoint
  to `telemetry_readings WHERE subject_kind='device'`.
- [ ] Analytics modules: `grep -rn "DeviceTelemetryModel\|TimescaleTelemetryRepository" src/`
  must return zero hits in `src/tagpulse/`. Test fakes are fine.
- [ ] External SQL clients: ask integration owners to confirm none
  point at the view.

### Execute the sunset migration (Sprint 21 — shipped)

When all preconditions are met, apply
[migration 032](../../migrations/versions/032_drop_legacy_device_telemetry.py):

1. Drops the `device_telemetry` SQL view.
2. Drops the `telemetry_readings_legacy_device` hypertable + RLS policy + `ix_device_telemetry_lookup` index.
3. `TimescaleTelemetryRepository` removed from `src/` (Sprint 14 surface folded into `TimescaleTelemetryReadingsRepository`).
4. `DeviceTelemetryModel` removed from `src/tagpulse/models/database.py`.
5. `GET /telemetry-models/{device_type}` 301 → `410 Gone` with a migration hint pointing at `/telemetry-models/device/{device_type}`.
6. ADR-013 marked superseded by ADR-014 / ADR-015.

**Downgrade is not data-reversible** — it re-creates empty hypertable +
RLS + view shells but cannot restore rows. Take a `pg_dump` of
`telemetry_readings_legacy_device` and keep it for at least one
retention window post-sunset; rollback is restore-from-backup.

Run the alembic round-trip harness against staging first:

```bash
TAGPULSE_INTEGRATION_DB_URL=postgresql+asyncpg://...:5432/staging \
  make migration-check
```

Followed by the production deploy procedure in
[CONTRIBUTING.md](../../CONTRIBUTING.md).

---

## Troubleshooting

| Symptom                                                      | Likely cause                                                                 |
|--------------------------------------------------------------|------------------------------------------------------------------------------|
| Tenant opted into `lot` but no lot rows appear               | EPCs not bound to `stock_items`. Check `stock_items.binding_value` matches.  |
| `lot.cold_chain_breach` rule never fires                     | Tenant didn't opt into `lot`. `subject_kind` mismatch silently drops events. |
| Repeated alerts every minute                                 | Rule's `cooldown_s` is 0. Default is 300s for `telemetry.threshold`.         |
| Alert message shows raw UUIDs instead of names               | Lot/asset name lookup is best-effort; UUIDs are the fallback.                |
| `make migration-check` fails after the cutover migration     | Downgrade path forgot to drop a constraint. Fix migration and re-run.        |
