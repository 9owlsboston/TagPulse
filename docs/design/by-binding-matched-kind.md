# Design: /assets/by-binding returns the matched binding_kind (I-WAPN)

**Sprint:** 83 · **Status:** proposed · **Date:** 2026-07-25
**Related:** [asset-display-label-vin-lookup.md](asset-display-label-vin-lookup.md) (I-P923 — the
endpoint this enriches), TagPulse-Mobile `I-WAPN` / `C-RYH7` INC2a

## Summary

`GET /assets/by-binding` (I-P923) resolves an active binding value → asset, but returns only the
`AssetResponse` — it doesn't say **which binding kind matched**. A VIN resolves via a `vin` binding
(a pure lookup handle that does **not** map-link tag-reads); if only a `vin` binding exists (no
`device`/`epc`/`tid` binding), a bind can "succeed" yet reads won't Map-link. The handset needs the
matched `binding_kind` (+ value) to warn the operator. This sprint enriches the response.

## Decision

Return **`AssetByBindingResponse`** — a subclass of `AssetResponse` adding `binding_kind` and
`binding_value` (the stored, canonical value that matched) at the top level. Subclassing keeps
every asset field flat (the handset reads `display_label`, `id`, `binding_kind` without nesting)
and is purely **additive** to the I-P923 shape (all `AssetResponse` fields remain). The mobile
client re-vendors `openapi.json` once and picks up `display_label` + `binding_kind` together
(it's currently thin-parsing, so no client breaks).

- `binding_kind` = the kind of the active binding whose value matched (`vin`/`device`/`epc`/`tid`).
  The handset warns when it's `vin` (lookup-only; won't map-link on its own).
- `binding_value` = the stored binding value that matched (canonical form, e.g. the upper-cased
  VIN) — lets the handset confirm exactly what it resolved.

## Changes

| Area | File | Change |
|---|---|---|
| Schema | `models/schemas.py` | `AssetByBindingResponse(AssetResponse)` + `binding_kind`, `binding_value` |
| Repo | `repositories/timescaledb/assets.py` | `get_by_binding_value` returns `AssetByBindingResponse` (SELECT adds `binding_kind`, `binding_value` from the matched binding) |
| Service | `api/services/asset_service.py` | return type update (delegates) |
| Route | `api/routes/assets.py` | `GET /assets/by-binding` `response_model=AssetByBindingResponse` |
| Tests | `tests/unit/` + `tests/integration/` | response carries the matched kind/value; the live-DB lookup returns `binding_kind='vin'` for a VIN |
| Contract | `openapi.json` | regenerate |
| Docs | this doc, CHANGELOG, roadmap §83 | enriched response |

## Non-goals
- No "does a `device` binding also exist?" computation — the mobile plan-duck asked for the
  **matched** kind; the handset's warning triggers on a `vin` match (per I-WAPN). A richer
  "binding coverage" summary is out of scope.
- No `?kind=` filter on the lookup (the alternative in the ask) — returning the matched kind is
  the lighter, sufficient change.

## Validation
- `make check` green; the C-6RTX integration test for `get_by_binding_value` extended to assert
  `binding_kind`.
- `openapi.json` regenerated; backend SHA recorded for the mobile client.

## Review attestations
- **Plan-stage rubber-duck:** ran — no blockers (Row access, model_dump round-trip, subclass
  response_model, additive compatibility all confirmed sound).
- **Diff-stage code-review:** ran — no blocking issues.
