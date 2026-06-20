# ADR-033: Asset `epc` bindings resolve against either the decoded EPC URI or the raw hex

- Status: **Accepted** (chore/epc-binding-match-hex, June 2026)
- Scope: `TagPulse` backend — binding resolution across the location/path/zone
  surfaces.
- Related: [ADR-028 (tags as a first-class entity)](028-tags-as-first-class-entity.md),
  `asset_current_location` view (migrations 024 → 056 → 057),
  Sprint 69 T1 (registrar `epc` vs `epc_hex` mismatch).

## Context

An asset is bound to a tag via `asset_tag_bindings (binding_value, binding_kind)`.
For `binding_kind='epc'` the **resolution join** that powers every "where is my
asset" surface matched **only the decoded EPC URI**:

```sql
(b.binding_kind = 'epc' AND tr.epc = b.binding_value)
```

`tag_reads` carries an EPC in **two** columns:

- `tr.epc` — the **decoded EPC URI** (e.g. `urn:epc:id:sgtin:0614141.812345.6789`),
  populated only when the raw hex decodes to a known scheme (SGTIN, SSCC).
- `tr.epc_hex` — the **raw hex** (e.g. `3034257BF461A84000030D40`).

`AssetTagBindingCreate.binding_value` is a free-form string (`min_length=1,
max_length=256`) with **no normalization or format validation**, so operators
can — and naturally do — paste whichever form they have. The WM reader fleet's
stable, visible identity is the **hex**. The result was a silent footgun:

- Bind with the **hex** → the location/path/zone joins (which match `tr.epc`)
  never resolve, so the asset shows `Location —` / `Last seen never` despite
  streaming reads.
- Worse, the surfaces were **inconsistent**: the dashboard asset-activity
  sparkline matched `tr.epc_hex`, while the location view matched `tr.epc` — so
  the same binding could "work" on one screen and not another.

This is the same `epc` vs `epc_hex` family as the Sprint 69 **T1** registrar bug.

## Decision

For `binding_kind='epc'`, resolution matches **either** the decoded URI **or**
the raw hex:

```sql
(b.binding_kind = 'epc' AND (tr.epc = b.binding_value OR tr.epc_hex = b.binding_value))
```

So an operator (or WM) may bind with **whichever EPC form they have** and every
read-resolution surface finds it. `binding_value` stays stored **verbatim**
(no migration of existing rows, no lossy normalization); we widen the *match*,
not the data. `tid` and `device` kinds are unchanged.

### Sites updated (every live read→binding resolution)

| Surface | Location |
|---|---|
| `asset_current_location` view (current location) | migration 057 (`reads_latest` + `geo_latest` CTEs) |
| `GET /assets/{id}/path` | `asset_location.py` `_PATH_SQL` |
| Assets-in-reader-bound-zone | `asset_location.py` `_ASSETS_IN_READER_BOUND_ZONE` |
| Overlapping-zones signaling | `signaling/overlapping_zones.py` `_READS_SQL` |
| Dashboard asset-activity sparkline | `services/dashboard.py` (was `tr.epc_hex`-only → now both) |

### Why match-both, not normalize-on-write

- **No data migration / no lossy choice.** Normalizing to one canonical form
  would require rewriting existing `binding_value` rows and picking a winner
  (URI vs hex) that can't always round-trip (raw/unknown-scheme tags have no
  URI). Matching both is additive and reversible.
- **Operator-form-agnostic.** The contract becomes "bind with any EPC form,"
  which is the least-surprising behavior.
- **Indexing.** These are bounded, tenant-scoped, time-windowed joins; the extra
  `OR tr.epc_hex = …` term is acceptable at current volumes. If it ever shows in
  the p95 SLO, add an index on `tag_reads (tenant_id, epc_hex)` (note in backlog).

## Consequences

- A hex binding now resolves location/path/zones/dashboard identically to a URI
  binding — the WM scenario works without operators knowing about URIs.
- **Deferred (gated-off):** the floor-position estimator's EPC→asset fusion
  ([`asset_fusion.py`](../../src/tagpulse/services/asset_fusion.py) /
  `floor_position_source.py`) still resolves bindings by the URI it reads from
  `tag_reads.epc`. It is gated **off** by default
  (`position_estimator_enabled=false`); extending it to match `epc_hex` requires
  plumbing `tr.epc_hex` through `RawRead` + the fusion lookup and is tracked as a
  backlog follow-up. No live surface is affected.
- A future hardening could **validate/normalize `binding_value` at write time**
  per `kind` (reject malformed EPCs, canonicalize) — out of scope here; this ADR
  fixes resolution without constraining input.
