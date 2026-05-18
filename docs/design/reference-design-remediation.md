# Reference-Design Remediation Plan

- Sprint: 33 (kickoff sprint — this doc + ADR stubs only; implementation lands across Sprints 34–39)
- Status: Proposed
- Owner: backend + UI joint
- Related:
  - Audit inputs (unversioned, sibling repo): `~/ws/TagPulse-Design/IMPLEMENTATION-GAPS.md`, `~/ws/TagPulse-Design/UI-LOOK-AND-FEEL-GAPS.md`
  - ADRs created by this plan: [019 Categories](../adr/019-categories.md), [020 Labels first-class](../adr/020-labels-first-class.md), [021 Configurable Sensing Events](../adr/021-configurable-sensing-events.md), [022 Soft Assets](../adr/022-soft-assets.md), [023 Outbound Connections MQTT/Kafka](../adr/023-outbound-connections-mqtt-kafka.md), [024 Indoor Position Estimation](../adr/024-position-estimation.md)

---

## 1 — Purpose

Two gap audits exist against the Wiliot Cloud Platform reference design (which
TagPulse uses as its visual + behavioural reference, not as a clone target):

| Audit | Scope | Where |
|---|---|---|
| `IMPLEMENTATION-GAPS.md` | Schema, services, APIs, payload envelopes | `~/ws/TagPulse-Design/` (unversioned) |
| `UI-LOOK-AND-FEEL-GAPS.md` | TagPulse-UI IA, theming, page layouts, form patterns | `~/ws/TagPulse-Design/` (unversioned) |

Together they enumerate ~25 distinct gaps tagged 🔴 (5) / 🟠 (12) / 🟡 (8).
**This document is the scope-lock**: which gaps we commit to, which we defer,
which we drop, and how they fan out across the next 6 sprints.

It is *not* a design doc for any one gap — each accepted gap gets its own ADR
(see §4) and, where it touches ≥3 components, a per-feature design doc in
`docs/design/`.

---

## 2 — Method

For every gap in both audits, force a **Commit / Defer / Drop** decision and
pin a sprint slot. The five 🔴 gaps drive sprint sequencing because they
unblock downstream UI work:

```
Sprint 33 (kickoff)        ──> this doc + 5 ADR stubs + UI quick-wins track  [✅ shipped]
Sprint 34 (categories)     ──> ADR 019 lands; assets.category_id; UI Categories page  [✅ shipped]
Sprint 35 (labels)         ──> ADR 020 lands; labels catalog; label chips replace metadata JSON  [✅ shipped — backend kickoff [#36](https://github.com/9owlsboston/TagPulse/pull/36), schema [#37](https://github.com/9owlsboston/TagPulse/pull/37), API [#38](https://github.com/9owlsboston/TagPulse/pull/38), filter [#39](https://github.com/9owlsboston/TagPulse/pull/39), docs [#40](https://github.com/9owlsboston/TagPulse/pull/40); UI catalog + Asset Detail chips TagPulse-UI [#30](https://github.com/9owlsboston/TagPulse-UI/pull/30) `d475c2e`. Follow-up UI work (chips on Site/Zone/Device/Category detail + `?labels[…]` filter strip on list pages) tracked under §3.2 rows 3.9a / 3.9b.]
Sprint 36 (sensing events) ──> ADR 021 lands; sensing_event_configs; new modal
Sprint 37 (connections)    ──> ADR 023 lands; MQTT dispatcher; Connections page redesign
Sprint 38 (edge)           ──> Bridge/Gateway split; Connectivity Monitor; OTA toggle
Sprint 39 (soft assets)    ──> ADR 022 lands; auto-create policy; convert-to-asset flow
```

**Parallel quick-wins track** (mostly no backend deps — #6 is a tiny backend slice; all ship during Sprint 33–34):

1. TagPulse-UI `<ConfigProvider>` + teal `colorPrimary` + light Sider.
2. Sider section groups (DATA MANAGEMENT / EDGE MANAGEMENT dividers).
3. Top-bar account-management dropdown — move admin items out of sidebar.
4. Reusable `<LastUpdate timestamp onRefresh/>` component.
5. **Light + Dark theme toggle** (Light is default). Ant Design `theme.algorithm` swap (`defaultAlgorithm` ↔ `darkAlgorithm`), preference persisted in `localStorage` under `tagpulse.theme`, honours `prefers-color-scheme` on first visit. Toggle lives in the Account dropdown from quick-win #3. Token overrides applied to both algorithms so the teal `colorPrimary` (and any later brand colour from quick-win #6) reads correctly in dark. Tracked in TagPulse-UI.
6. **Per-tenant branding** — logo + display name + optional brand colour. **Has a small backend slice in TagPulse** (this repo, shipped this sprint as migration 036 + `GET`/`PATCH /tenant/branding` + unauthenticated `GET /branding/{slug}` for the login page) plus a UI form in TagPulse-UI. See §3.3 below.

These six PRs together close ~30 % of the perceptual gap at near-zero
engineering cost and are tracked in the TagPulse-UI repo, except for the
branding backend slice which lands here in Sprint 33.

---

## 3 — Scope decisions

### 3.1 Backend gaps (`IMPLEMENTATION-GAPS.md`)

| # | Gap | Severity | Decision | Sprint | Notes |
|---|---|---|---|---|---|
| 2.1 | Categories entity | 🔴 | **✅ Done** ([#31](https://github.com/9owlsboston/TagPulse/pull/31), [`d6dec19`](https://github.com/9owlsboston/TagPulse/commit/d6dec19)) | 34 | ADR 019. Unblocks 2.3, 2.8, 2.14. |
| 2.2 | Labels first-class | 🔴 | **✅ Done** (kickoff [#36](https://github.com/9owlsboston/TagPulse/pull/36) `fd38046` · schema [#37](https://github.com/9owlsboston/TagPulse/pull/37) `c5db1a7` · API [#38](https://github.com/9owlsboston/TagPulse/pull/38) `fe0b732` · filter [#39](https://github.com/9owlsboston/TagPulse/pull/39) `4493a02` · docs [#40](https://github.com/9owlsboston/TagPulse/pull/40) `05d48c1`; UI catalog + Asset Detail chips TagPulse-UI [#30](https://github.com/9owlsboston/TagPulse-UI/pull/30) `d475c2e`; UI chips on Site/Zone/Device/Category TagPulse-UI [#37](https://github.com/9owlsboston/TagPulse-UI/pull/37) `eb54523`) | 35 | ADR 020 ratified. Backend catalog + per-entity associations (`/labels`, `/{entity_segment}/{id}/labels`) + deep-object `?labels[KEY]=V1,V2` filter on `GET /assets`/`/sites`/`/zones`/`/devices`. `metadata` JSONB retained as the escape hatch for non-catalogued attributes. UI Phase D shipped: admin-only `/admin/labels` catalog management page (linked from the Account dropdown — admin chrome lives there per QW3, intentionally not in the Sider) + reusable `<LabelChips entityType entityId/>` chip strip wired into Asset Detail. Catalog-key autocomplete on the chip popover is filtered by `entity_type` so users only see legal keys for the entity they're tagging. Sprint 37 row 3.9a extends chip parity to Site / Zone / Device / Category — all five entity types can now attach catalog labels from their detail surface (modal-based for Site/Zone/Category, classic tabbed page for Device). Remaining UI work tracked under row 3.9b (filter strip exposing the Phase C deep-object filter on the four list pages) and 3.9d (inline Label picker in the Create flow). |
| 2.2a | Labels Phase B — orphan `entity_labels` cleanup on hard-delete entity handlers | 🟠 | **✅ Done** ([#47](https://github.com/9owlsboston/TagPulse/pull/47), [`7ce3292`](https://github.com/9owlsboston/TagPulse/commit/7ce3292)) | 39 | ADR 020 explicitly notes "Orphan cleanup happens in the entity-delete handlers (Phase B)" but Sprint 35 row 2.2 shipped only Phases A/C/D/E. Sites, zones, and categories hard-delete via `await self._session.delete(row)` in their respective repository methods with no preceding cleanup, leaving `entity_labels` rows pointing at non-existent `entity_id`s. `count_associations()` on the parent label joins the catalog table for tenant scope but cannot join the entity table (it's polymorphic) — orphan rows therefore inflate the count forever, returning a 409 `association_count > 0` on `DELETE /labels/{id}` even after every visible entity has dropped the label. Fix adds `TimescaleLabelRepository.delete_for_entity(tenant_id, entity_type, entity_id) -> int` and wires it into the three hard-delete repository methods *before* the entity row is removed (sites/zones cascade; categories cascade after the `CategoryInUseError` guard). Assets (soft-delete to `retired`) and devices (soft-delete via `POST /{id}/decommission`) intentionally keep their label associations through the lifecycle and are not touched. |
| 2.3 | Configurable Sensing Events | 🔴 | **Commit** | 36 | ADR 021 **v2**: extend `rules` (8 nullable columns + new `sensing.<event_type>.<trigger>` condition types). Discarded v1's parallel-table approach after first-review push-back — 60% overlap with existing `rules`/`alerts` didn't justify a second CRUD surface. All four event types (Location/Geolocation/Temperature/Geofencing) ship in one migration in S36. |
| 2.4 | Soft Assets | 🔴 | **Commit (deferred)** | 39 | ADR 022. Has cost implications (one row per unique stray pixel); slot last so we can size based on observed `tag_reads_without_asset_total` in dev. |
| 2.5 | MQTT/Kafka/Pub-Sub Connections | 🔴 | **Commit (MQTT only)** | 37 | ADR 023. Kafka + Pub-Sub deferred to backlog; covers only ~5 % of expected enterprise integrations and each is a separate dispatcher. |
| 2.6 | `users.role` adds `installer` | 🟠 | **✅ Done** ([#31](https://github.com/9owlsboston/TagPulse/pull/31), [`d6dec19`](https://github.com/9owlsboston/TagPulse/commit/d6dec19)) | 34 | Rode along with the Categories migration (`users.role` CHECK extended in `user_schemas.py`). |
| 2.7 | `sites.kind` + `latitude` + `longitude` + structured address | 🟠 | **✅ Done** ([#33](https://github.com/9owlsboston/TagPulse/pull/33), [`9ab21fd`](https://github.com/9owlsboston/TagPulse/commit/9ab21fd)) | 34 | Migration 038 + `SiteUpdate` 422 hardening; UI surface shipped in TagPulse-UI [#19](https://github.com/9owlsboston/TagPulse-UI/pull/19). |
| 2.8 | `assets.category_id` FK + `external_ref` validation | 🟠 | **✅ Done** ([#31](https://github.com/9owlsboston/TagPulse/pull/31), [`d6dec19`](https://github.com/9owlsboston/TagPulse/commit/d6dec19)) | 34 | Same migration as Categories. `external_ref` validator added in `schemas.py` (rejects `. : / ? # \ [ ] @ , | & ! = $ ' * + ; %`). |
| 2.8a | `GET /assets?category_id=` server-side filter | 🟡 | **✅ Done** ([#43](https://github.com/9owlsboston/TagPulse/pull/43), [`2f732f1`](https://github.com/9owlsboston/TagPulse/commit/2f732f1)) | 37 | Small backend slice promoting Category from a client-side filter to a server-side one. New optional `Query` param on `GET /assets` threads through `AssetService.list_assets` and `TimescaleAssetRepository.list` as a `WHERE assets.category_id = ?` predicate; combines with `asset_type`/`status`/`q`/`labels[…]` via AND. Unblocks the UI half of row 3.3a's list-header filter — the UI stops doing client-side filtering and just passes the param through. |
| 2.9 | Outbound event envelope (`confidence`, `keySet[]`, `eventConfigurationId`, `categoryId`, `labels[]`) | 🟠 | **Commit** | 36 | Lands with ADR 021 v2 in a dispatcher-layer module. Fires for **all** rules — legacy rules get `confidence=1.0`, `keySet=[]`, `categoryId=null`, `labels=[]`. Additive to existing webhook payloads. |
| 2.10 | Per-catalog API security keys + JWT exchange | 🟠 | **Defer** | backlog | Today's auth (per-tenant + per-user + per-device tokens) satisfies the security model. Revisit if a customer asks for per-catalog scoping. |
| 2.11 | Bridge OTA toggle + gateway-driven push | 🟡 | **Commit** | 38 | Lands with Bridge/Gateway split; minimal — just `devices.configuration.ota_upgrade_enabled` + edge contract field. |
| 2.12 | Bridge Survey tool | 🟡 | **Drop** | — | Highly specific to BLE relay deployments; TagPulse's RFID-reader primary use case doesn't need it. Document as out-of-scope. |
| 2.13 | Connectivity Monitor (uptime % / disconnections / avg) | 🟠 | **Commit** | 38 | Lands with Bridge/Gateway split. Single analytics module over existing `device_health` + `last_seen` data. |
| 2.14 | Pixel registry (batch CSV 6 000, reel-range, transfer) | 🟡 | **Defer** | backlog | TagPulse intentionally has no `tags` table (see [data-models.md](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table)). A read-only Pixels page can be served from `asset_tag_bindings` + `tag_reads` without a new entity — that's a UI ticket, not a backend one. |
| 2.15 | Connections Import/Export JSON + per-conn rate-limit + Monitor | 🟠 | **Commit (rate-limit + monitor only)** | 37 | Import/Export deferred — low value vs. cost. |
| 2.16 | Auto-association by reel range | 🟡 | **Drop** | — | Depends on pixel-registry (2.14, deferred). Drop entirely; manual association is sufficient. |
| 2.17 | First-party Python SDK on PyPI | 🟡 | **Defer** | backlog | The generated OpenAPI client serves SDK needs today. |
| 2.18 | Indoor position estimation (multi-reader triangulation in grid zones) | 🟠 | **Commit** | 40 | ADR 024. Surfaced post-audit by SME review of football-field-size deployments with a 400×600 XY grid of fixed readers. Five of seven prerequisites already in place (`tag_reads.signal_strength`/`reader_antenna`/`sensor_data`/`tag_data`, reader-side `telemetry_readings subject_kind='device'`). Adds `devices.position_x/y/z`, `sites.coord_system JSONB`, new `asset_positions` hypertable, third `processor='trilateration'` value on the ADR 021 v2 enum, one reference algorithm (`weighted_centroid_log_distance`), pluggable algorithm interface, BYO-precomputed-positions ingest path for customers running Zebra/Impinj/Mojix/RFcode RTLS. Adds `devices/{device_id}/status` MQTT topic for reader-level periodic status (reader temp, RSSI baseline, position update for mobile readers). |

### 3.2 UI gaps (`UI-LOOK-AND-FEEL-GAPS.md`)

| # | Gap | Severity | Decision | Sprint | Notes |
|---|---|---|---|---|---|
| 1.1 | Sider section groups | 🔴 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | Ant Menu `type: 'group'` headers: DATA MANAGEMENT / EDGE MANAGEMENT / INVENTORY. Admin items moved up into the Account dropdown (next row). |
| 1.1 | Categories nav item | 🔴 | **✅ Done** (TagPulse-UI [#19](https://github.com/9owlsboston/TagPulse-UI/pull/19), [`7954bc6`](https://github.com/9owlsboston/TagPulse-UI/commit/7954bc6)) | 34 | `TagsOutlined`, `minRole: 'viewer'`, between Assets and Sites in `Layout.tsx`. |
| 1.1 | Pixels nav item (read-only page) | 🟠 | **Commit** | 38 | UI-only; reads from existing bindings + reads. |
| 1.1 | Bridges vs Gateways sidebar split | 🟠 | **Commit** | 38 | Driven by `devices.device_role` (see ADR 011 — already partially scoped). |
| 1.1 | Unify Telemetry/Models/Rules/Alerts → "Sensing Events & Data" | 🟠 | **Commit** | 36 | Free with ADR 021 v2 — one backend table = one consolidated UI list. Legacy condition types remain editable under a "Legacy rule" sub-tab. |
| 1.1 | Admin items → top-right Account dropdown | 🟠 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | Avatar + Dropdown trigger in the Header; admin-only menu group exposes Tenant Settings / Branding / Usage / Users / Audit Log / Dead Letters plus the QW5 dark-mode `Switch`. Sidebar lost all admin entries as a result. |
| 1.2 | Notification bell | 🟡 | **Defer** | backlog | Useful but not on critical path. |
| 2.1 | Light Sider + blue-600 `colorPrimary` + ConfigProvider | 🟠 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | `src/theme/ThemeProvider.tsx` wraps the app in an AntD `ConfigProvider`; default `colorPrimary = #2563eb` (Tailwind blue-600) matching the reference design. Light Sider is the default; dark Sider activates with QW5's theme toggle. |
| 2.2 | Typography polish | 🟡 | **Defer** | backlog | Marginal value. |
| 3.1 | Onboarding cards on Dashboard | 🟡 | **Drop** | — | TagPulse's operator-dashboard model is intentional. Keep as-is. |
| 3.2 | Locations/Zones two-tab redesign + Soft Assets column | 🟠 | **✅ Done (tabs)** (TagPulse-UI [#19](https://github.com/9owlsboston/TagPulse-UI/pull/19), [`7954bc6`](https://github.com/9owlsboston/TagPulse-UI/commit/7954bc6)); **Commit (Soft Assets column)** | 34 (tabs) + 39 (Soft Assets column) | Tabs + kind icons + count tags shipped. Soft Assets column still waits for ADR 022. |
| 3.3 | Categories page | 🔴 | **✅ Done** (TagPulse-UI [#19](https://github.com/9owlsboston/TagPulse-UI/pull/19), [`7954bc6`](https://github.com/9owlsboston/TagPulse-UI/commit/7954bc6)) | 34 | Full CRUD; 409 conflict UI surfaces backend's `asset_count`; `category_type` immutable on edit per ADR 019. |
| 3.3a | Category wiring on Asset CRUD surfaces (Create / Edit modal Select + List column + Detail row + optional list filter) | 🟠 | **✅ Done** (TagPulse-UI [#36](https://github.com/9owlsboston/TagPulse-UI/pull/36) [`ea53e63`](https://github.com/9owlsboston/TagPulse-UI/commit/ea53e63) + [#44](https://github.com/9owlsboston/TagPulse-UI/pull/44) [`289cea2`](https://github.com/9owlsboston/TagPulse-UI/commit/289cea2)) | 37 (CRUD wiring) + 38 (server-side list filter) | Gap surfaced 2026-05-18 while reviewing Sprint 35 doc audit — the `/categories` page exists and the backend `assets.category_id` FK has been live since Sprint 34 (`d6dec19`, row 2.8), but the Asset Create modal, Edit modal, list table, and Detail Descriptions never got the picker. Net effect: users can manage the catalog but cannot actually attach a Category to any asset from the UI. Scope: AntD `<Select>` populated from `useCategories()` in the Create + Edit Asset modals (allowClear; sorted by `category_type` + name); new "Category" column in `AssetList` (renders `category.name` with a `Tag` coloured by `category_type` enum value, sortable by name); new Descriptions row in `AssetDetail` Overview tab; optional `?category_id=` query filter in the list header (parity with Status filter — server-side support shipped under §3.1 row 2.8a, the UI just passes the param through). UI #36 shipped the modals + column + detail row and the initial (client-side) list filter; UI #44 caught the list filter up to the backend by regenerating the API client to expose `categoryId` on `listAssetsAssetsGet`, extending `useAssets()` with a `category_id?: string` param, and dropping the in-memory narrow in `AssetList` so toggling Categories now causes a fresh server-paginated fetch keyed by the active filter. |
| 3.4 | Pixels page | 🟠 | **Commit** | 38 | UI-only as noted above. |
| 3.5 | Sensing Events modal | 🔴 | **Commit** | 36 | New "Add Sensing Event" modal per reference layout. Form posts to `/v1/.../sensing-events` which resolves to `RuleService` with `kind=sensing` filter (ADR 021 v2). |
| 3.6 | Connections page redesign | 🟠 | **Commit** | 37 | |
| 3.7 | Gateways list with status banner + Compliance panel | 🟠 | **Commit (status banner only)** | 38 | Compliance panel **dropped** — Compliance Status is bridge-firmware-specific and TagPulse's RFID-reader use case has no equivalent. |
| 3.8 | Per-device Monitor tab w/ Connectivity KPIs + 8h/1d/3d range | 🟠 | **Commit** | 38 | Lands with Connectivity Monitor backend (2.13). |
| 3.9 | Asset Detail Labels chips | 🟠 | **✅ Done** (TagPulse-UI [#30](https://github.com/9owlsboston/TagPulse-UI/pull/30), [`d475c2e`](https://github.com/9owlsboston/TagPulse-UI/commit/d475c2e)) | 35 | `<LabelChips entityType="asset" entityId={id}/>` shipped under the Descriptions on the Overview tab. Chips render `key: value` AntD `<Tag closable>` with per-catalog color fallback; `+ Add label` popover does AutoComplete on the tenant's catalog (filtered by `entity_type`) + free-text value. Role-gated (editor+ can add/remove). Soft cap 30 enforced API-side. The Events Log tab is tracked separately as row 3.9c. |
| 3.9a | Labels chips on Site / Zone / Device / Category detail pages | 🟠 | **✅ Done** (TagPulse-UI [#37](https://github.com/9owlsboston/TagPulse-UI/pull/37), [`eb54523`](https://github.com/9owlsboston/TagPulse-UI/commit/eb54523)) | 37 | `<LabelChips/>` wired into all four remaining entity types. **DeviceDetail** gets the classic AssetDetail treatment — chip strip directly under the Overview-tab metadata `<Descriptions>`. **Sites / Zones / Categories** don't have separate detail routes — they're managed through Edit modals — so chips live at the bottom of each Edit modal (guarded with `editingSite && / editingZone && / editing &&` so they mount only when an entity is selected; Create modals untouched — that's row 3.9d). No backend changes, no new components, no new hooks, no new deps — pure wiring. All 5 entity types (`asset` / `site` / `zone` / `device` / `category`) now have label-chip parity. The 30-association soft cap, RBAC gating (viewer = read-only, editor+ can add/remove), catalog-key autocomplete filtered by `entity_type`, friendly 404 toast when a key isn't in the catalog, and admin deep-link to `/admin/labels` all come for free since they live inside `<LabelChips/>` itself. |
| 3.9b | Label filter strip on Assets / Sites / Zones / Devices list pages | 🟠 | **✅ Done** (TagPulse-UI [#39](https://github.com/9owlsboston/TagPulse-UI/pull/39), [`2f815a6`](https://github.com/9owlsboston/TagPulse-UI/commit/2f815a6)) | 37 | New reusable `<LabelFilterStrip/>` (controlled, URL-friendly) wired into AssetList, SitesZones (two strips — site- and zone-scoped), and DeviceList. Surfaces the Phase C `?labels[KEY]=V1,V2` deep-object filter via a dashed `+ Filter by label` Tag → Popover with AutoComplete key input (scoped to the entity's catalog) + free-text value input. Each accepted pair lands as an AntD `Tag` chip grouped under its key, with per-value close-X removal, per-key X dropping the whole group, and a trailing Clear link. Companion `src/lib/labelFilter.ts` pure-helpers (`normalize`/`encode`/`parse`/`apply`) keep the URL contract in one place and round-trip with `URLSearchParams`. Client-side validation mirrors the server contract (key/value regex, 5-key cap, 20-value-per-key cap, duplicate `(key,value)` no-op). Four list hooks (`useAssets`/`useDevices`/`useSites`/`useZones`) gained an optional `labels?: LabelFilter` param that routes through the hand-written `request()` helper when set; otherwise unchanged. 34 vitest files / 155 tests pass; tsc + eslint clean; `build:smoke` boots. |
| 3.9c | Asset Detail Events Log tab | 🟠 | **✅ Done** (TagPulse-UI [#43](https://github.com/9owlsboston/TagPulse-UI/pull/43), [`7c04b36`](https://github.com/9owlsboston/TagPulse-UI/commit/7c04b36)) | 38 | New `Events Log` tab on Asset Detail synthesizes a chronological timeline client-side from data the page already fetches (asset + bindings + external positions) — no extra network calls and no new backend endpoint, so it's available immediately for every tenant. Pure helper `src/lib/assetEvents.ts` emits `created` / `updated` / `retired` / `bound` / `unbound` / `external_position` events sorted newest-first, with a `UPDATE_NOISE_FLOOR_MS = 1_000` floor that suppresses the immediate post-create UPDATE that some asset writers emit, and emits `retired` (not `updated`) when `status === 'retired'`. Presentational `src/components/AssetEventsTab.tsx` renders an AntD `<Table>` with When / Type / Summary columns, a `KIND_COLOR` map for at-a-glance scanning, and an `Empty` fallback. 9 helper tests + 3 component tests, total 171/171 vitest pass; tsc + eslint clean; `build:smoke` boots. True audit-log integration deferred — the backend `/admin/audit-logs` endpoint is admin-only and doesn't accept a `resource_id` filter; the synthesis approach captures the same operator view today and can be swapped for a real audit-log feed once the backend exposes one. |
| 3.9d | Inline Label picker in Create Asset modal (and equivalents on Site / Zone / Device / Category Create) | 🟠 | **✅ Done** (TagPulse-UI [#38](https://github.com/9owlsboston/TagPulse-UI/pull/38), [`6969c30`](https://github.com/9owlsboston/TagPulse-UI/commit/6969c30)) | 37 | New reusable `<PendingLabelPicker/>` (Create-flow sibling of `<LabelChips/>`) — pure client-side queue (`PendingLabel[]` controlled by `value` + `onChange`) that the parent flushes after the Create response returns the new `entity_id`. UX mirrors `<LabelChips/>` exactly (same Add chip / popover / key autocomplete / free-text value / 30-cap / RBAC gate / catalog-empty fallback). Companion `attachPendingLabels(entityType, entityId, pending)` helper iterates the queue, POSTs each association via the generated `LabelsService.associateLabelEntitySegmentEntityIdLabelsPost`, and returns `{ ok, failed: { label, error }[] }`. Wired into five Create flows: AssetList (Register Asset modal), SitesZones (Create Site/Transporter + Create Zone modals), CategoryList (Create Category modal), and DeviceRegister (full-page form). 32 vitest files / 121 tests pass; tsc + eslint clean; `build:smoke` boots. Backend unchanged (`POST /{entity_segment}/{id}/labels` already exists). |
| 3.10 | Developer Portal landing card | 🟡 | **Defer** | backlog | Link out to Swagger UI from Dashboard footer is sufficient short-term. |
| §4 | Modal width / sticky footer / Advanced accordion patterns | 🟡 | **Commit (opportunistic)** | rolling | Adopt as each affected page is touched, not as a dedicated sprint. |
| §5 | Two-line cells / kebab-menu action column | 🟡 | **Commit (opportunistic)** | rolling | Same — adopt during each page's redesign. |
| §6 | Empty-state component | 🟡 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | `<EmptyState title description illustration action/>` wrapper at `src/components/EmptyState.tsx`. Consumers adopted incrementally as each zero-rows surface is touched. |

### 3.3 Additions surfaced during planning (not in the original audits)

| # | Item | Severity | Decision | Sprint | Notes |
|---|---|---|---|---|---|
| QW4 | `<LastUpdate timestamp onRefresh/>` widget | 🟡 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | Reusable header widget at `src/components/LastUpdate.tsx` — renders "Updated 2m ago" (rolling on a 30-s `setInterval`) + an optional refresh `Button`. Tooltip shows absolute timestamp. Drop-in for any list/dashboard header; consumers adopted incrementally. |
| QW5 | Light + Dark theme toggle | 🟠 | **✅ Done** (TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 (quick-win) | `src/theme/ThemeProvider.tsx` swaps `theme.algorithm` between `defaultAlgorithm` ↔ `darkAlgorithm`, persists choice to `localStorage['tagpulse.theme']`, honours `prefers-color-scheme` on first visit. Toggle lives in the Account dropdown (QW3). Default = Light. |
| QW6 | Per-tenant branding (logo + display name + brand colour) | 🟠 | **✅ Done** (backend [#30](https://github.com/9owlsboston/TagPulse/pull/30), [`35de092`](https://github.com/9owlsboston/TagPulse/commit/35de092); UI TagPulse-UI [#20](https://github.com/9owlsboston/TagPulse-UI/pull/20), [`ae0fd81`](https://github.com/9owlsboston/TagPulse-UI/commit/ae0fd81)) | 33 | Two-part. **Backend slice (this repo, Sprint 33 — shipped):** Alembic migration `036_tenant_branding.py` adds `tenants.logo_url VARCHAR(2048) NULL`, `tenants.display_name VARCHAR(255) NULL`, `tenants.brand_color VARCHAR(7) NULL`. New router `src/tagpulse/api/routes/tenant_branding.py` exposes three endpoints under the existing `/tenant/*` convention (no `/v1` prefix is used anywhere in this codebase): `GET /tenant/branding` (any authenticated role; current-tenant read), `PATCH /tenant/branding` (admin; PATCH semantics — explicit `null` clears an override; audited via `tenant.branding.update`), and the unauthenticated `GET /branding/{slug}` so the login page can skin itself before the user has credentials. API-level validation is intentionally format-only: HTTPS scheme on `logo_url`, ≤2048-char URL, ≤255-char display name, `^#[0-9A-Fa-f]{6}$` brand colour. The HEAD-based content-length (≤ 2 MiB) and `image/*` MIME-type check originally listed here is **deferred to the operator's upload/CDN tier** — it requires an outbound HTTP call from the API on every PATCH and is brittle (DoS / latency / mocking surface). No secret material involved — logo is a public-by-design URL the operator hosts (CDN, blob storage, public website). **UI slice (TagPulse-UI, Sprint 33 — shipped):** Admin-only Branding form at `/admin/branding` (linked from the Account dropdown) with `display_name`, `logo_url`, and `brand_color (#RRGGBB)` inputs + live preview; empty fields are PATCHed as explicit `null` to clear the override. `<BrandSync>` side-effect component pushes the authenticated tenant's `brand_color` into ThemeProvider so the whole AntD tree adopts the colour. Sider header reads `display_name ?? user.tenant_name ?? tenantId ?? 'TagPulse'` and renders the `logo_url` `<img>` (graceful onError fallback). Login page (`TenantGuard`) honours a `?tenant=<slug>` query param: fetches `GET /branding/{slug}` via `usePublicBranding()`, renders the tenant logo + display name above the login form, and skins the Sign-In button via `setBrandColor`. Brand colour, when set, overrides the default Tailwind blue-600 (`#2563eb`) `colorPrimary` at `ConfigProvider` level. |

Neither item warrants its own ADR — QW5 is a one-file `ConfigProvider` change
and QW6 is three nullable columns + one PATCH endpoint. Both are documented
here as the system of record. If branding grows (multi-region logos,
favicons, email-template theming), promote to ADR-024 at that point.

---

## 4 — ADRs created by this plan

Each blocking-tier gap gets a stub ADR landed in this PR so reviewers can
debate the *shape* before the implementing sprint loads its first line of
code:

| ADR | Title | Sprint that implements |
|---|---|---|
| [019](../adr/019-categories.md) | Categories as a first-class entity | 34 |
| [020](../adr/020-labels-first-class.md) | Labels first-class (catalog + per-entity associations) | 35 |
| [021](../adr/021-configurable-sensing-events.md) | Configurable Sensing Events (replacing rules-only) | 36 |
| [022](../adr/022-soft-assets.md) | Soft Assets auto-creation policy | 39 |
| [023](../adr/023-outbound-connections-mqtt-kafka.md) | Outbound Connections — add MQTT dispatcher | 37 |
| [024](../adr/024-position-estimation.md) | Indoor position estimation — trilateration processor + `asset_positions` | 40 |

Each stub captures: known context, the four candidate options seen in the
audit + design references, the recommended option with rationale, and the
open questions for the implementing sprint to close.

---

## 5 — What this plan deliberately does **not** commit to

So that future audits don't refile these as gaps:

| Item | Why excluded |
|---|---|
| 1:1 pixel-perfect parity with the reference UI | Different audience (operator-heavy, multi-mode); TagPulse keeps draggable dashboard, Map page, Polygon zones, Path replay, Bulk Reassign Zone, `<ApiHealthGate>`, `<RoleGuard>`, CSV Import, mTLS for MQTT, RLS multi-tenancy — see [§7 of IMPLEMENTATION-GAPS.md](~/ws/TagPulse-Design/IMPLEMENTATION-GAPS.md) and [§9 of UI-LOOK-AND-FEEL-GAPS.md](~/ws/TagPulse-Design/UI-LOOK-AND-FEEL-GAPS.md). |
| First-class `pixels` table | Intentional per [data-models.md](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table). Read-only page from bindings is sufficient. |
| Kafka + Pub-Sub dispatchers | Marginal use case. Re-evaluate when a customer asks. |
| Per-catalog security keys + JWT exchange | Existing tenant/user/device token model satisfies our threat model. |
| Bridge OTA full firmware-push pipeline | Toggle field only; the actual firmware-distribution mechanism stays an edge-side concern. |
| Bridge Survey tool | RFID-reader use case doesn't need it. |
| Pixel batch CSV (up to 6 000) + reel-range ops + cross-account transfer | Depends on the dropped pixel registry. |
| Compliance Status panel on Gateways list | RFID readers don't have the bridge-firmware compliance concept. |
| Developer Portal landing page | Out-of-scope for a single-product SaaS at this stage. |
| Bell-icon notifications | Nice-to-have, deferred indefinitely. |
| Sub-meter indoor positioning accuracy out of the box | ADR 024 ships pluggable triangulation + one reference algorithm. Customers needing sub-meter precision use the BYO-positions ingest path (POST to `/v1/asset-positions` from their vendor's SDK) or replace the bundled algorithm. |
| Phase-angle / FMCW / UWB-anchor positioning | Data shape doesn't fit `tag_reads`; no committed customer ask. Out-of-scope until demanded (then a new processor + columns). |
| Real-time RTLS vendor replacement (Zebra Aurora, Impinj ItemSense, Mojix, RFcode) | TagPulse is the platform of record + integration fabric, not a competing RTLS engine. Interop via the BYO-positions path. |
| Automated reader-position calibration | Reader (x, y) is operator-supplied. Algorithms that derive reader position from observed tag patterns are out-of-scope. |

---

## 6 — Acceptance criteria for this kickoff PR

- [x] This document lands at `docs/design/reference-design-remediation.md`.
- [x] Six ADR stubs (019–024) land at `docs/adr/0NN-*.md` with status **Proposed**.
- [x] `docs/adr/README.md` index appended with the six new rows.
- [x] `CHANGELOG.md` `## Unreleased` section gains a "Docs" entry.
- [x] `make check` clean (545 passed, 1 skipped).
- [x] PR description links to this plan and the two source audits.
- [x] Quick-wins additions QW5 (Dark theme) + QW6 (Per-tenant branding) recorded in §2 and §3.3.
- [x] Post-audit gap 2.18 (indoor position estimation, SME-surfaced) recorded in §3.1, with ADR 024 stub + four matching scope-outs in §5.

---

## 7 — Updating this document

When a subsequent sprint implements one of the committed gaps:

1. Flip the row's decision column from **Commit** to **Done** + link the
   implementing PR.
2. Move any new gaps discovered during implementation into the table with a
   fresh decision.
3. Annotate any sprint slot that slips with the new target sprint and a
   one-line reason.

When a deferred or dropped gap is re-litigated (e.g. customer ask):
re-open the row, change the decision, and link the new ADR.

The audits in `~/ws/TagPulse-Design/` are **read-only snapshots** as of
2026-05-17. Don't re-edit them — produce a new dated audit if the
reference design itself evolves.
