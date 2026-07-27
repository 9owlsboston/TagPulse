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

> **Start here:** [`phased-strategy.md`](phased-strategy.md) is the **capstone** — it sequences
> every note below into one dependency-ordered path (no-regret moves → STR-noise beachhead →
> insurance-leak expansion → AI/data platform).

| Note | Theme | One-line thesis |
|---|---|---|
| [`phased-strategy.md`](phased-strategy.md) | **Capstone — phased synthesis** | Sequences all notes by dependency and maps each move to the roadmap entry / ADR / design doc it would seed. |
| [`ai-landscape.md`](ai-landscape.md) | AI opportunity map | The existing telemetry pipeline, analytics-module framework, and edge gateway are an unusually AI-ready substrate — four clean plug-in patterns. |
| [`sensor-wedges.md`](sensor-wedges.md) | Sensor market wedges | The `GatewayDriver` seam generalizes OBD-II; each new cheap/standardized sensor is a new driver, not a rebuild. |
| [`home-automation.md`](home-automation.md) | Home-automation wedge | A stationary MQTT hub (the `clients/pi` lineage) meets a market that already speaks MQTT — strong as a B2B fleet-of-homes play. |
| [`home-automation-market.md`](home-automation-market.md) | Home-automation market data | Sourced (secondary) sizing for the five B2B fleet-of-homes verticals; STR-noise or insurance-leak are the cleanest beachheads. |
| [`beachhead-str-vs-insurance.md`](beachhead-str-vs-insurance.md) | Beachhead decision | Enter via STR-noise (fast, direct SaaS), expand into insurance-leak (channel, bigger) on a shared sensor+platform; the real fork is the hardware posture. |
| [`actuation-control-loop.md`](actuation-control-loop.md) | Actuation / control loop | Adding a "device command" rule action turns TagPulse from a monitoring platform into an automation platform — a cross-wedge category upgrade. |
| [`edge-ai-architecture.md`](edge-ai-architecture.md) | Edge-AI architecture | The differentiator isn't a model, it's a model *lifecycle* on the `GatewayDriver` seam: inference stage, signed OTA model delivery, retraining flywheel. |
| [`competitive-positioning.md`](competitive-positioning.md) | Competitive positioning | Most incumbents sit in different layers; the wedge is vertical depth on cheap sensors + an AI-native layer, not out-breadthing Samsara or out-plumbing hyperscalers. |
| [`data-monetization.md`](data-monetization.md) | Data monetization | A multi-tenant platform accrues a cross-tenant data asset; monetize via privacy-preserving benchmarks/indices/federated models — trust and opt-in are the whole game. |

## Adding a note

1. On a `chore/<topic>` branch (docs-only, no roadmap-item change) or the active kickoff
   branch if it accompanies planned work — never on `main` directly.
2. One file per theme, kebab-case. Follow the [§7 documentation output style](../../AGENTS.md)
   (summary first, why before how, cite or flag, link don't duplicate).
3. Ground claims in the codebase where possible; flag external/market claims `unverified`.
4. Add a row to the table above, update `CHANGELOG.md` under `## Unreleased`, open a PR.
5. When a note earns scheduled work, add the roadmap entry and link back to the note.
