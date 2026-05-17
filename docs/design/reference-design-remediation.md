# Reference-Design Remediation Plan

- Sprint: 33 (kickoff sprint — this doc + ADR stubs only; implementation lands across Sprints 34–39)
- Status: Proposed
- Owner: backend + UI joint
- Related:
  - Audit inputs (unversioned, sibling repo): `local reference notes/IMPLEMENTATION-GAPS.md`, `local reference notes/UI-LOOK-AND-FEEL-GAPS.md`
  - ADRs created by this plan: [019 Categories](../adr/019-categories.md), [020 Labels first-class](../adr/020-labels-first-class.md), [021 Configurable Sensing Events](../adr/021-configurable-sensing-events.md), [022 Soft Assets](../adr/022-soft-assets.md), [023 Outbound Connections MQTT/Kafka](../adr/023-outbound-connections-mqtt-kafka.md), [024 Indoor Position Estimation](../adr/024-position-estimation.md)

---

## 1 — Purpose

Two gap audits exist against the external IoT cloud-platform reference design (which
TagPulse uses as its visual + behavioural reference, not as a clone target):

| Audit | Scope | Where |
|---|---|---|
| `IMPLEMENTATION-GAPS.md` | Schema, services, APIs, payload envelopes | `local reference notes/` (unversioned) |
| `UI-LOOK-AND-FEEL-GAPS.md` | TagPulse-UI IA, theming, page layouts, form patterns | `local reference notes/` (unversioned) |

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
Sprint 33 (kickoff)        ──> this doc + 5 ADR stubs + UI quick-wins track
Sprint 34 (categories)     ──> ADR 019 lands; assets.category_id; UI Categories page
Sprint 35 (labels)         ──> ADR 020 lands; labels catalog; label chips replace metadata JSON
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
| 2.1 | Categories entity | 🔴 | **Commit** | 34 | ADR 019. Unblocks 2.3, 2.8, 2.14. |
| 2.2 | Labels first-class | 🔴 | **Commit** | 35 | ADR 020. Replaces free-form `metadata` JSONB for catalogued use cases; raw `metadata` stays for true bag-of-properties. |
| 2.3 | Configurable Sensing Events | 🔴 | **Commit** | 36 | ADR 021 **v2**: extend `rules` (8 nullable columns + new `sensing.<event_type>.<trigger>` condition types). Discarded v1's parallel-table approach after first-review push-back — 60% overlap with existing `rules`/`alerts` didn't justify a second CRUD surface. All four event types (Location/Geolocation/Temperature/Geofencing) ship in one migration in S36. |
| 2.4 | Soft Assets | 🔴 | **Commit (deferred)** | 39 | ADR 022. Has cost implications (one row per unique stray tag); slot last so we can size based on observed `tag_reads_without_asset_total` in dev. |
| 2.5 | MQTT/Kafka/Pub-Sub Connections | 🔴 | **Commit (MQTT only)** | 37 | ADR 023. Kafka + Pub-Sub deferred to backlog; covers only ~5 % of expected enterprise integrations and each is a separate dispatcher. |
| 2.6 | `users.role` adds `installer` | 🟠 | **Commit** | 34 | Trivial; ride along with the Categories migration. |
| 2.7 | `sites.kind` + `latitude` + `longitude` + structured address | 🟠 | **Commit** | 34 | Required for Site/Transporter icon column in Locations UI. |
| 2.8 | `assets.category_id` FK + `external_ref` validation | 🟠 | **Commit** | 34 | Same sprint as Categories — they're one migration. |
| 2.9 | Outbound event envelope (`confidence`, `keySet[]`, `eventConfigurationId`, `categoryId`, `labels[]`) | 🟠 | **Commit** | 36 | Lands with ADR 021 v2 in a dispatcher-layer module. Fires for **all** rules — legacy rules get `confidence=1.0`, `keySet=[]`, `categoryId=null`, `labels=[]`. Additive to existing webhook payloads. |
| 2.10 | Per-catalog API security keys + JWT exchange | 🟠 | **Defer** | backlog | Today's auth (per-tenant + per-user + per-device tokens) satisfies the security model. Revisit if a customer asks for per-catalog scoping. |
| 2.11 | Bridge OTA toggle + gateway-driven push | 🟡 | **Commit** | 38 | Lands with Bridge/Gateway split; minimal — just `devices.configuration.ota_upgrade_enabled` + edge contract field. |
| 2.12 | Bridge Survey tool | 🟡 | **Drop** | — | Highly specific to BLE relay deployments; TagPulse's RFID-reader primary use case doesn't need it. Document as out-of-scope. |
| 2.13 | Connectivity Monitor (uptime % / disconnections / avg) | 🟠 | **Commit** | 38 | Lands with Bridge/Gateway split. Single analytics module over existing `device_health` + `last_seen` data. |
| 2.14 | deferred tag registry (batch CSV 6 000, reel-range, transfer) | 🟡 | **Defer** | backlog | TagPulse intentionally has no `tags` table (see [data-models.md](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table)). A read-only Tags page can be served from `asset_tag_bindings` + `tag_reads` without a new entity — that's a UI ticket, not a backend one. |
| 2.15 | Connections Import/Export JSON + per-conn rate-limit + Monitor | 🟠 | **Commit (rate-limit + monitor only)** | 37 | Import/Export deferred — low value vs. cost. |
| 2.16 | Auto-association by reel range | 🟡 | **Drop** | — | Depends on tag-registry (2.14, deferred). Drop entirely; manual association is sufficient. |
| 2.17 | First-party Python SDK on PyPI | 🟡 | **Defer** | backlog | The generated OpenAPI client serves SDK needs today. |
| 2.18 | Indoor position estimation (multi-reader triangulation in grid zones) | 🟠 | **Commit** | 40 | ADR 024. Surfaced post-audit by SME review of football-field-size deployments with a 400×600 XY grid of fixed readers. Five of seven prerequisites already in place (`tag_reads.signal_strength`/`reader_antenna`/`sensor_data`/`tag_data`, reader-side `telemetry_readings subject_kind='device'`). Adds `devices.position_x/y/z`, `sites.coord_system JSONB`, new `asset_positions` hypertable, third `processor='trilateration'` value on the ADR 021 v2 enum, one reference algorithm (`weighted_centroid_log_distance`), pluggable algorithm interface, BYO-precomputed-positions ingest path for customers running Zebra/Impinj/Mojix/RFcode RTLS. Adds `devices/{device_id}/status` MQTT topic for reader-level periodic status (reader temp, RSSI baseline, position update for mobile readers). |

### 3.2 UI gaps (`UI-LOOK-AND-FEEL-GAPS.md`)

| # | Gap | Severity | Decision | Sprint | Notes |
|---|---|---|---|---|---|
| 1.1 | Sider section groups | 🔴 | **Commit** | 33 (quick-win) | Ant Menu `type: 'group'`. Zero backend dep. |
| 1.1 | Categories nav item | 🔴 | **Commit** | 34 | Lands with backend Categories. |
| 1.1 | Tags nav item (read-only page) | 🟠 | **Commit** | 38 | UI-only; reads from existing bindings + reads. |
| 1.1 | Bridges vs Gateways sidebar split | 🟠 | **Commit** | 38 | Driven by `devices.device_role` (see ADR 011 — already partially scoped). |
| 1.1 | Unify Telemetry/Models/Rules/Alerts → "Sensing Events & Data" | 🟠 | **Commit** | 36 | Free with ADR 021 v2 — one backend table = one consolidated UI list. Legacy condition types remain editable under a "Legacy rule" sub-tab. |
| 1.1 | Admin items → top-right Account dropdown | 🟠 | **Commit** | 33 (quick-win) | Pure UI refactor. |
| 1.2 | Notification bell | 🟡 | **Defer** | backlog | Useful but not on critical path. |
| 2.1 | Light Sider + teal `colorPrimary` + ConfigProvider | 🟠 | **Commit** | 33 (quick-win) | Highest visual-impact-per-LOC change in the audit. |
| 2.2 | Typography polish | 🟡 | **Defer** | backlog | Marginal value. |
| 3.1 | Onboarding cards on Dashboard | 🟡 | **Drop** | — | TagPulse's operator-dashboard model is intentional. Keep as-is. |
| 3.2 | Locations/Zones two-tab redesign + Soft Assets column | 🟠 | **Commit** | 34 (tabs) + 39 (Soft Assets column) | Tabs ride with `sites.kind`; Soft Assets column waits for ADR 022. |
| 3.3 | Categories page | 🔴 | **Commit** | 34 | |
| 3.4 | Tags page | 🟠 | **Commit** | 38 | UI-only as noted above. |
| 3.5 | Sensing Events modal | 🔴 | **Commit** | 36 | New "Add Sensing Event" modal per reference layout. Form posts to `/v1/.../sensing-events` which resolves to `RuleService` with `kind=sensing` filter (ADR 021 v2). |
| 3.6 | Connections page redesign | 🟠 | **Commit** | 37 | |
| 3.7 | Gateways list with status banner + Compliance panel | 🟠 | **Commit (status banner only)** | 38 | Compliance panel **dropped** — Compliance Status is bridge-firmware-specific and TagPulse's RFID-reader use case has no equivalent. |
| 3.8 | Per-device Monitor tab w/ Connectivity KPIs + 8h/1d/3d range | 🟠 | **Commit** | 38 | Lands with Connectivity Monitor backend (2.13). |
| 3.9 | Asset Detail Events Log tab + Labels chips | 🟠 | **Commit** | 35 (chips) + 36 (Events Log) | |
| 3.10 | Developer Portal landing card | 🟡 | **Defer** | backlog | Link out to Swagger UI from Dashboard footer is sufficient short-term. |
| §4 | Modal width / sticky footer / Advanced accordion patterns | 🟡 | **Commit (opportunistic)** | rolling | Adopt as each affected page is touched, not as a dedicated sprint. |
| §5 | Two-line cells / kebab-menu action column | 🟡 | **Commit (opportunistic)** | rolling | Same — adopt during each page's redesign. |
| §6 | Empty-state component | 🟡 | **Commit** | 33 (quick-win) | `<EmptyState illustration title action/>` wrapper; 1-day UI ticket. |

### 3.3 Additions surfaced during planning (not in the original audits)

| # | Item | Severity | Decision | Sprint | Notes |
|---|---|---|---|---|---|
| QW5 | Light + Dark theme toggle | 🟠 | **Commit** | 33 (quick-win) | Pure TagPulse-UI. Ant Design `ConfigProvider` with `theme.algorithm` swap. Token overrides ensure teal/brand colour remains legible in dark. Persisted to `localStorage`; first visit reads `prefers-color-scheme`. Default = Light to match reference design. |
| QW6 | Per-tenant branding (logo + display name + brand colour) | 🟠 | **Commit** | 33 | Two-part. **Backend slice (this repo, Sprint 33 — shipped):** Alembic migration `036_tenant_branding.py` adds `tenants.logo_url VARCHAR(2048) NULL`, `tenants.display_name VARCHAR(255) NULL`, `tenants.brand_color VARCHAR(7) NULL`. New router `src/tagpulse/api/routes/tenant_branding.py` exposes three endpoints under the existing `/tenant/*` convention (no `/v1` prefix is used anywhere in this codebase): `GET /tenant/branding` (any authenticated role; current-tenant read), `PATCH /tenant/branding` (admin; PATCH semantics — explicit `null` clears an override; audited via `tenant.branding.update`), and the unauthenticated `GET /branding/{slug}` so the login page can skin itself before the user has credentials. API-level validation is intentionally format-only: HTTPS scheme on `logo_url`, ≤2048-char URL, ≤255-char display name, `^#[0-9A-Fa-f]{6}$` brand colour. The HEAD-based content-length (≤ 2 MiB) and `image/*` MIME-type check originally listed here is **deferred to the operator's upload/CDN tier** — it requires an outbound HTTP call from the API on every PATCH and is brittle (DoS / latency / mocking surface). No secret material involved — logo is a public-by-design URL the operator hosts (CDN, blob storage, public website). **UI slice (TagPulse-UI, Sprint 33–34):** Account dropdown → Branding form with URL input + live preview + colour picker; Sider header reads `display_name ?? name` and renders `<img src={logo_url}/>` with a tasteful default; login page hits `GET /branding/{slug}` (unauthenticated) using the tenant-slug subdomain or query param. Brand colour, when set, overrides the default teal `colorPrimary` at `ConfigProvider` level. |

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
| 1:1 visual-perfect parity with the reference UI | Different audience (operator-heavy, multi-mode); TagPulse keeps draggable dashboard, Map page, Polygon zones, Path replay, Bulk Reassign Zone, `<ApiHealthGate>`, `<RoleGuard>`, CSV Import, mTLS for MQTT, RLS multi-tenancy — see [§7 of IMPLEMENTATION-GAPS.md](local reference notes/IMPLEMENTATION-GAPS.md) and [§9 of UI-LOOK-AND-FEEL-GAPS.md](local reference notes/UI-LOOK-AND-FEEL-GAPS.md). |
| First-class `tags` table | Intentional per [data-models.md](../data-models.md#where-is-the-tag-and-why-theres-no-tags-table). Read-only page from bindings is sufficient. |
| Kafka + Pub-Sub dispatchers | Marginal use case. Re-evaluate when a customer asks. |
| Per-catalog security keys + JWT exchange | Existing tenant/user/device token model satisfies our threat model. |
| Bridge OTA full firmware-push pipeline | Toggle field only; the actual firmware-distribution mechanism stays an edge-side concern. |
| Bridge Survey tool | RFID-reader use case doesn't need it. |
| Tag batch CSV (up to 6 000) + reel-range ops + cross-account transfer | Depends on the dropped deferred tag registry. |
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

The audits in `local reference notes/` are **read-only snapshots** as of
2026-05-17. Don't re-edit them — produce a new dated audit if the
reference design itself evolves.
