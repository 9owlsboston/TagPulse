# Strategy — future-direction exploration

**Status:** living index · **Owner:** product/eng

## Summary

This folder holds **forward-looking product and market strategy** for TagPulse — the
exploration that *feeds* the roadmap, one level above the per-change design docs. Read a note
here to understand **where the platform could go and why**; read [`docs/design/`](../design/)
to understand **how a committed change works**, and [`docs/roadmap.md`](../roadmap.md) for
**what is actually scheduled**.

These are **explorations, not commitments.** Nothing here is a decision until it surfaces as a
roadmap item, a [`docs/design/`](../design/) doc, or an [ADR](../adr/README.md). Market claims
are flagged `unverified` where they aren't grounded in the codebase.

## How this relates to the rest of the docs

```
docs/strategy/   →  where could we go, and why        (this folder — exploration)
        │
        ▼
docs/roadmap.md  →  what is scheduled                 (single planning source of truth)
        │
        ▼
docs/design/     →  how a committed change works       (the "why" of a specific change)
docs/adr/        →  a specific decision, recorded       (ADRs)
```

A strategy note **matures** by seeding one or more roadmap entries; the detailed *how* then
lands in a design doc or ADR on a sprint/kickoff branch. Until then it stays here as a
living document.

## Notes

| Note | Theme | One-line thesis |
|---|---|---|
| [`ai-landscape.md`](ai-landscape.md) | AI opportunity map | The existing telemetry pipeline, analytics-module framework, and edge gateway are an unusually AI-ready substrate — four clean plug-in patterns. |
| [`sensor-wedges.md`](sensor-wedges.md) | Sensor market wedges | The `GatewayDriver` seam generalizes OBD-II; each new cheap/standardized sensor is a new driver, not a rebuild. |
| [`home-automation.md`](home-automation.md) | Home-automation wedge | A stationary MQTT hub (the `clients/pi` lineage) meets a market that already speaks MQTT — strong as a B2B fleet-of-homes play. |

## Adding a note

1. On a `chore/<topic>` branch (docs-only, no roadmap-item change) or the active kickoff
   branch if it accompanies planned work — never on `main` directly.
2. One file per theme, kebab-case. Follow the [§7 documentation output style](../../AGENTS.md)
   (summary first, why before how, cite or flag, link don't duplicate).
3. Ground claims in the codebase where possible; flag external/market claims `unverified`.
4. Add a row to the table above, update `CHANGELOG.md` under `## Unreleased`, open a PR.
5. When a note earns scheduled work, add the roadmap entry and link back to the note.
