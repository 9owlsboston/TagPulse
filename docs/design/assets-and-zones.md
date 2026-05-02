# Design Document: Assets & Zones (Sprint 15)

**Date:** 2026-05-02
**Status:** proposed
**Related:** [asset-tracking-gap-analysis.md](asset-tracking-gap-analysis.md) (A3, A4), [telemetry-and-location.md](telemetry-and-location.md), [tracking-modes.md](tracking-modes.md), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md), [data-models.md](../data-models.md)

> **Scope note:** this document covers the **asset-tracking** domain layer with **fixed readers**. The **inventory-tracking** sibling (`products`, `stock_items`, `stock_movements`) is specified in [tracking-modes.md](tracking-modes.md) and lands in Sprint 15b. **Mobile readers** (vehicle-mounted, forklift, handheld) and **carrier / manifest** semantics (truck-with-cargo) are specified in [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md). All of these share the substrate defined here — `sites`, `zones`, the ingestion enrichment, and the `subject.zone_changed` event.

---

## 1. Problem Statement

Assets exist today only as opaque `tag_id` strings on `tag_reads`. The platform cannot answer:

- *Where is asset "Forklift-12" right now?*
- *Which assets are in zone "Cold-Storage-A"?*
- *When did asset X enter the loading dock?*

There is also no concept of a **place**. "DockDoor-3" lives as a freeform string in `devices.metadata`. Rules cannot fire on zone transitions because zones don't exist.

This sprint introduces **Assets** (the thing being tracked) and **Sites/Zones** (where) — both first-class entities with CRUD APIs, RLS, and admin UI surfaces. Zones are reader-bound only this sprint; geofence polygons land in Sprint 17.

---

## 2. Scope

In scope:

- `assets`, `asset_tag_bindings` tables + REST CRUD.
- `sites`, `zones` tables + REST CRUD (reader-bound).
- `asset_current_location` SQL view.
- Ingestion enrichment: emit `asset.zone_changed` event on reader transitions across zone boundaries.
- Repository helpers: `get_assets_in_zone`, `get_asset_path`.
- Simulator: bind tag IDs to named assets; cross zones over time.
- UI parity (see §7).

Out of scope:

- Polygon zones / geofence transitions (Sprint 17).
- Map visualization (Sprint 17).
- Geofence rule conditions (Sprint 17).
- Asset hierarchy / containment (e.g., pallet-on-truck) — backlog.

---

## 3. Data Model

### 3.1 Assets

```sql
CREATE TABLE assets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    external_ref  VARCHAR(255) NULL,         -- ERP/WMS asset code
    name          VARCHAR(255) NOT NULL,
    asset_type    VARCHAR(50)  NOT NULL,     -- 'pallet' | 'tool' | 'container' | …
    status        VARCHAR(20)  NOT NULL DEFAULT 'active',  -- 'active' | 'retired' | 'lost'
    metadata      JSONB        NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, external_ref)
);

CREATE INDEX ix_assets_tenant_type ON assets (tenant_id, asset_type);

ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
CREATE POLICY assets_tenant_isolation ON assets
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

`asset_type` is free-form per tenant (no enum table for now); UI offers a typeahead from existing values.

> **No `quantity` column — by design.** Each `assets` row represents one physical thing; counts of alike assets come from `SELECT count(*) … GROUP BY asset_type`. If you need quantity-of-alike-units (e.g., "how many of SKU X are in Cold-Storage"), that's *inventory mode* — use `stock_items` instead. Rationale and FAQ in [tracking-modes.md §2.1](tracking-modes.md).

### 3.2 Tag bindings

```sql
CREATE TABLE asset_tag_bindings (
    asset_id    UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id      VARCHAR(256) NOT NULL,
    bound_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    unbound_at  TIMESTAMPTZ NULL,
    tenant_id   UUID NOT NULL,
    PRIMARY KEY (asset_id, tag_id, bound_at)
);

-- Active bindings per tag (for tag_id → asset lookup at ingest time).
CREATE UNIQUE INDEX ix_asset_tag_bindings_active
  ON asset_tag_bindings (tenant_id, tag_id)
  WHERE unbound_at IS NULL;

-- Lookup by asset.
CREATE INDEX ix_asset_tag_bindings_by_asset
  ON asset_tag_bindings (asset_id, bound_at DESC);

ALTER TABLE asset_tag_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY asset_tag_bindings_tenant_isolation ON asset_tag_bindings
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

Invariant: at any time, a `tag_id` has at most one active binding per tenant (enforced by partial unique index). Replacing a tag = `UPDATE … SET unbound_at = now() WHERE asset_id = ? AND unbound_at IS NULL` then `INSERT`.

> **Naming note.** The actual column is `binding_value` (the table is new in Sprint 15 and ships with the right name from day one); the `tag_id` references in this section's SQL examples predate that naming decision — read them as `binding_value`. `binding_kind='device'` and the `external_locations` table land in the same sprint per [mobile-carriers-and-manifests.md §10](mobile-carriers-and-manifests.md).

### 3.3 Sites and zones

```sql
CREATE TABLE sites (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id),
    name              VARCHAR(255) NOT NULL,
    address           TEXT NULL,
    default_timezone  VARCHAR(64) NOT NULL DEFAULT 'UTC',
    metadata          JSONB NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE zones (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES tenants(id),
    site_id            UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name               VARCHAR(255) NOT NULL,
    kind               VARCHAR(20)  NOT NULL,  -- 'reader_bound' | 'geofence' (geofence in S17)
    fixed_reader_ids   JSONB        NULL,      -- array of device UUIDs (reader_bound)
    polygon_geojson    JSONB        NULL,      -- reserved for Sprint 17
    metadata           JSONB        NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (site_id, name),
    CHECK ((kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL)
        OR (kind = 'geofence'    AND polygon_geojson  IS NOT NULL))
);

CREATE INDEX ix_zones_tenant ON zones (tenant_id);
```

Both tables RLS-enabled, identical pattern to `assets`.

### 3.4 Current-location view

```sql
CREATE VIEW asset_current_location AS
SELECT
    b.asset_id,
    b.tenant_id,
    tr.device_id      AS last_reader_id,
    tr.timestamp      AS last_seen_at,
    tr.latitude,
    tr.longitude,
    tr.signal_strength
FROM asset_tag_bindings b
JOIN LATERAL (
    SELECT *
    FROM tag_reads
    WHERE tag_reads.tenant_id = b.tenant_id
      AND tag_reads.tag_id    = b.tag_id
      AND tag_reads.timestamp >= b.bound_at
      AND (b.unbound_at IS NULL OR tag_reads.timestamp <= b.unbound_at)
    ORDER BY tag_reads.timestamp DESC
    LIMIT 1
) tr ON true
WHERE b.unbound_at IS NULL;
```

View inherits RLS from base tables. Materialized variant deferred — start as plain view, watch query plans.

---

## 4. APIs

```
POST   /assets                       create
GET    /assets                       list (filter: type, status, q)
GET    /assets/{id}                  detail (+ active binding + current location)
PATCH  /assets/{id}                  update name/type/status/metadata
DELETE /assets/{id}                  soft delete (status='retired')
POST   /assets/{id}/bindings         bind tag {tag_id}
DELETE /assets/{id}/bindings/{tag}   unbind (sets unbound_at)
GET    /assets/{id}/path             list of (timestamp, reader_id, zone_id, lat, lon)
                                      params: since, until, limit (default 200)

POST   /sites                        create
GET    /sites                        list
GET    /sites/{id}                   detail (+ zones summary)
PATCH  /sites/{id}                   update
DELETE /sites/{id}                   delete (cascades zones)

POST   /sites/{site_id}/zones        create zone
GET    /zones                        list (filter: site_id)
GET    /zones/{id}                   detail (+ assets currently in zone)
GET    /zones/{id}/assets            assets currently in zone
PATCH  /zones/{id}                   update reader_ids / metadata
DELETE /zones/{id}                   delete
```

Permissions:

| Route | Role |
|---|---|
| `GET …` | viewer+ |
| `POST/PATCH/DELETE assets, bindings` | editor+ |
| `POST/PATCH/DELETE sites, zones` | admin |

Standard tenant scoping via `require_role()`; all queries filter by `current_setting('app.current_tenant')` through RLS.

---

## 5. Ingestion Enrichment

When a `tag_read` is persisted, the ingestion service:

1. Looks up the active `asset_id` for `(tenant_id, tag_id)` (cached, TTL 60 s).
2. Looks up the `zone_id` (if any) covering the read's `device_id` (cached).
3. Compares against the **last known zone** for that asset (in-memory LRU + Redis-like fallback later — not this sprint).
4. If changed, publishes:

```python
EventBus.publish("subject.zone_changed", {
    "tenant_id": …, "subject_kind": "asset", "subject_id": <asset_id>,
    "from_zone_id": …, "to_zone_id": …,
    "device_id": …, "tag_id": …, "epc": …, "tid": …,
    "timestamp": …,
})
```

The inventory branch (Sprint 15b, [tracking-modes.md](tracking-modes.md) §5) emits the same topic with `subject_kind='stock_item'`. Rules engine matches on `subject_kind` so asset and inventory rules stay independent.

Rules engine subscribes in Sprint 17. For Sprint 15, the event is recorded in `audit_logs` with `event_source='ingestion'` and surfaced in the UI's "recent path" timeline.

A read with no asset binding is **not** an error — most reads will lack one until operators register assets. Counter `tag_reads_without_asset` exposed via Prometheus.

---

## 6. Repositories

```python
class AssetRepository:
    async def list(self, *, asset_type: str | None, status: str | None, q: str | None,
                   limit: int, offset: int) -> list[Asset]: ...
    async def get(self, asset_id: UUID) -> Asset | None: ...
    async def get_by_tag(self, tag_id: str) -> Asset | None: ...   # active binding
    async def get_path(self, asset_id: UUID, since: datetime,
                       until: datetime, limit: int) -> list[PathPoint]: ...

class ZoneRepository:
    async def list(self, *, site_id: UUID | None) -> list[Zone]: ...
    async def get_zone_for_reader(self, device_id: UUID) -> Zone | None: ...
    async def get_assets_in_zone(self, zone_id: UUID) -> list[Asset]: ...
```

`get_zone_for_reader` is the hot path — cached at the service layer with invalidation on zone updates (single-process LRU; multi-worker invalidation is acceptable to lag by TTL).

---

## 7. UI Parity

| Page | Change |
|---|---|
| Sidebar | Add **Assets**, **Sites & Zones** entries; admin-only on Sites & Zones writes |
| Assets (new) | List with search by name/external_ref/tag, filter by type/status, "Register asset" CTA |
| Asset detail (new) | Header (name, type, status), Current location card (reader, zone, last seen, mini-map), Bindings table (active + history), Recent path timeline (reader hops) |
| Sites & Zones (new) | Site list, "New site" form, per-site zone list with reader picker (multi-select from device registry) |
| Zone detail (new) | Reader chips, **Assets currently in zone** table |
| Device detail | New "Covers zones" panel listing zones whose `fixed_reader_ids` include this device |
| Data Explorer | Optional "Asset" column (joined via active binding) |
| Overview dashboard | New KPI tile: **Active assets** |

UI parity is a **release gate**.

---

## 8. Simulator Updates

`scripts/simulate_devices.py`:

- Auto-creates 5 assets per tenant on startup (idempotent), binds each to a synthetic tag.
- Defines 2 sites × 3 zones each at startup if absent.
- Walks assets through a state machine: idle → in transit → docked → idle, with reader assignments matching zones.
- Each step produces realistic `tag_reads`, exercising `asset.zone_changed`.

This guarantees a fresh `docker-compose up` shows non-empty Assets, Sites, and zone-transition audit entries within 60 s.

---

## 9. Testing Strategy

- Unit: binding lifecycle (bind / unbind / re-bind / partial unique index).
- Unit: `get_zone_for_reader` cache hit/miss/invalidate.
- Unit: `asset.zone_changed` only fires on transition (not on dwell).
- Integration: ingestion → zone change event → audit row + UI feed.
- Integration: `GET /zones/{id}/assets` returns expected assets after simulated traffic.
- Migration: 017 upgrade + downgrade green; view recreated cleanly.

---

## 10. Rollout

1. Migration 017 (additive).
2. Deploy backend; new routes feature-flag-free (read endpoints harmless on empty tables).
3. UI deploy.
4. Simulator update last so demo data appears.

Rollback: drop new tables (no FK from existing tables), revert UI build.

---

## 11. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 1 | Asset containment (`parent_asset_id`)? | **Promoted into Sprint 15** — `assets.parent_asset_id` and `stock_items.parent_stock_item_id` are now in scope per [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md) §4. |
| 2 | Bulk CSV import of assets + bindings? | **Backlog (G9 bulk ops).** Not gating Sprint 15. |
| 3 | Tag reuse across tenants? | **Per-tenant uniqueness, with global awareness for support.** The active-binding partial unique index stays per-tenant (tenant isolation preserved). Add a non-unique global index on `asset_tag_bindings(tag_id) WHERE unbound_at IS NULL` to support: (a) an **admin-only** API `GET /admin/tag-collisions?tag_id=…` returning the count of other tenants with an active binding (never the tenant identities), (b) a **bulk-import preflight** that flags "X of N bindings collide with another tenant" without revealing which, and (c) a Prometheus counter `tag_collisions_global_total` so we have early warning if collision rates climb (e.g., shared-3PL deployments, EPC re-encoding in the wild). No tenant-facing API surface changes. RLS unchanged. |
| 4 | Zone overlap (one reader, multiple zones)? | **Assume one zone per reader.** If two are configured, `get_zone_for_reader` returns the one with the lowest `created_at` deterministically and writes an audit-log warning flagging the ambiguity. |
| 5 | Asset hard delete? | **Retire-only via API** (`status='retired'`). Hard delete exposed only via an admin tool, not a public endpoint. Preserves audit history. |

### Still open

_(none currently — all open questions resolved.)_
