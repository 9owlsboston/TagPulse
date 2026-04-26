# ADR-007: Admin UI Technology Selection (Deferred)

**Status:** proposed
**Date:** 2026-04-25

## Context

TagPulse needs an admin UI for device management, telemetry dashboards, rule/alert configuration, and integration management. The UI is planned for Q3 2026 (Milestone 8). We need to decide on a frontend framework and hosting strategy.

This ADR is **proposed, not accepted** — the decision will be finalized when Milestone 8 begins, informed by team skills and any constraints discovered during Q2.

## Options Under Consideration

### Option A: React + Vite (SPA served by FastAPI)
- **Pros:** Large ecosystem, strong component libraries (Ant Design, MUI), good charting libraries for telemetry dashboards. Can be served as static files from the FastAPI backend.
- **Cons:** Separate build pipeline, adds Node.js to the dev toolchain.

### Option B: HTMX + Jinja2 (server-rendered by FastAPI)
- **Pros:** No separate frontend build, no JavaScript framework to maintain, stays in the Python ecosystem. Good for CRUD-heavy admin interfaces.
- **Cons:** Weaker for complex real-time dashboards (telemetry charts, live updates). Fewer UI component libraries.

### Option C: Grafana (dashboards) + lightweight admin UI
- **Pros:** Grafana excels at time-series visualization and connects directly to TimescaleDB. Admin CRUD could be a thin custom UI.
- **Cons:** Two systems to maintain. Grafana customization is limited for non-dashboard workflows (device registration, rule builder).

## Decision

Deferred to Q3 2026. The API-first design in Q2 ensures all UI capabilities are backed by REST endpoints, so the frontend choice doesn't block backend work.

## Action Items

- [ ] Evaluate team frontend skills at Q3 kickoff
- [ ] Build a prototype of the telemetry dashboard view in the leading candidate
- [ ] Finalize this ADR and update status to `accepted`
