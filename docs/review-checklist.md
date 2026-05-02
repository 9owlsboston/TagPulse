# Design Review Checklist

**Date:** 2026-05-02
**Scope:** Documents produced or substantially modified during the Sprint 14–17b design pass (telemetry/location, RFID tag identity, asset vs inventory tracking modes, edge device contract, device identity roadmap, geofencing, hardware primer).

Tick off each document as you review it. Suggested order is top-to-bottom; Tier 1 frames everything else.

---

## Tier 1 — Read first (cross-cutting decisions)

- [ ] [docs/design/tracking-modes.md](design/tracking-modes.md) — **NEW.** Asset vs inventory as sibling domain layers on shared substrate; `tenants.tracking_modes` flag; `subject.zone_changed` unified event. *Frames everything below.*
- [ ] [docs/design/rfid-tag-data-model.md](design/rfid-tag-data-model.md) — **NEW.** TID / EPC / user-memory column split, EPC decoder, tag-borne sensor mirror (decisions D1–D6).
- [ ] [docs/refs/edge-hardware-and-rfid-primer.md](refs/edge-hardware-and-rfid-primer.md) — **NEW.** RFID 101, hardware tiers, non-RFID peripheral integration patterns. Non-normative but sets vocabulary.

## Tier 2 — Sprint design docs (one per planned sprint)

- [ ] [docs/design/telemetry-and-location.md](design/telemetry-and-location.md) — **Sprint 14.** Location columns, `device_telemetry` hypertable, new MQTT topics, tag-borne sensor mirror wiring.
- [ ] [docs/design/assets-and-zones.md](design/assets-and-zones.md) — **Sprint 15.** Now scoped to asset-tracking mode + fixed readers; cross-links to tracking-modes.md and mobile-carriers-and-manifests.md; event renamed to `subject.zone_changed`.
- [ ] [docs/design/mobile-carriers-and-manifests.md](design/mobile-carriers-and-manifests.md) — **NEW.** Mobile readers (vehicles, forklifts, handhelds), carrier containment (`assets.parent_asset_id`, `stock_items.parent_stock_item_id`), three communication patterns (manifest / re-scan / cold-chain), edge-agent location throttling, `binding_kind='device'`.
- [ ] [docs/design/llm-integration-strategy.md](design/llm-integration-strategy.md) — **NEW (strategy, not sprint).** Server-side LLM is the default; edge SLM is parking-lot. Defines the `src/tagpulse/ai/` integration surface, tool-calling discipline, multi-tenancy + safety model, and AI Phases 1–4 in the backlog.
- [ ] [docs/design/edge-device-contract.md](design/edge-device-contract.md) — **Sprint 16.** Normative wire contract (dedup, ENTER/EXIT, batching, clock, heartbeat, buffer, reconnect, conformance).
- [ ] [docs/design/geofencing-and-map.md](design/geofencing-and-map.md) — **Sprint 17a.** Polygon zones, point-in-polygon, map UI; supports both asset markers and stock-density layers.

## Tier 3 — ADR + index

- [ ] [docs/adr/011-device-identity-roadmap.md](adr/011-device-identity-roadmap.md) — **NEW.** Token rotation (Phase 1) → mTLS (Phase 2) → hardware-backed keys (Phase 3).
- [ ] [docs/adr/README.md](adr/README.md) — verify ADR-011 listed correctly.

## Tier 4 — Reference / planning artifacts (skim for consistency)

- [ ] [docs/roadmap.md](roadmap.md) — Sprints 14, 15, **15b (new)**, 16, 17a, 17b; design cross-link blocks on Sprints 1–13; backlog additions (pallet hierarchy, cycle counts, kits, cross-mode hierarchy).
- [ ] [docs/data-models.md](data-models.md) — ER overview, all planned tables (assets/zones, products/lots/stock_items, telemetry, RFID identity columns), `tenants.tracking_modes`, RLS table, EventBus topics table, migrations 016–020.
- [ ] [docs/design/asset-tracking-gap-analysis.md](design/asset-tracking-gap-analysis.md) — original gap audit; refreshed for hardware-agnostic wording (Pi → edge device).

## Tier 5 — Edge client surface

- [ ] [clients/pi/README.md](../clients/pi/README.md) — generalized title/intro; verify install instructions still match what your audience expects.
- [ ] [CHANGELOG.md](../CHANGELOG.md) — Unreleased section: edge client entry, new primer entry, earlier asset-tracking entries.

---

## Suggested one-sitting review order (~50 min)

1. **tracking-modes.md** (10 min) — sets the frame
2. **roadmap.md** Sprints 14, 15, 15b (5 min) — confirms scope
3. **rfid-tag-data-model.md** (10 min) — touches all ingestion paths
4. **edge-device-contract.md + ADR-011** (15 min) — normative; hardest to change later
5. **data-models.md** (10 min) — the ER picture
6. Everything else as time allows

---

## What to look for while reviewing

- **Scope creep** — anything that should be deferred to backlog?
- **Naming consistency** — `subject.zone_changed`, `binding_kind`, `tracking_modes`, `tag_data` used the same way everywhere?
- **Cross-link freshness** — does each design doc point to the others it depends on?
- **UI parity** — every backend feature has a corresponding UI bullet on the same sprint?
- **Migration numbering** — 016 → 020 are sequential and conflict-free?
- **RLS coverage** — every new tenant-scoped table has an RLS policy listed in [data-models.md](data-models.md)?
- **Hardware-agnostic wording** — any leftover "Raspberry Pi" prose that should say "edge device"?

## After review

- Capture any rejected scope as backlog items in [roadmap.md](roadmap.md).
- Open ADR drafts for any decision flagged as "needs ADR" during review.
- Update [CHANGELOG.md](../CHANGELOG.md) Unreleased section if new docs land from this review.
