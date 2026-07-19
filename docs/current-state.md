# Current state — TagPulse

> **Snapshot:** 2026-07-19. The single, always-current answer to *"where is this
> project right now?"* — a **supplement to the README**, not a design-doc rollup.
> Lead with a human summary (a short plain-English paragraph a newcomer reads
> top-to-bottom *without* opening links, then a diagram); keep the rest thin —
> one line per area + **links** to the authoritative topic docs. On any conflict,
> the linked topic doc wins. Update this doc and bump the snapshot date as the
> **last step** of any change that moves the current state.

## Summary

TagPulse is the **backend** of a two-repo IoT platform (the admin SPA lives in
[TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI)). It ingests device telemetry —
first device type is RFID tag readers — over **MQTT and HTTP**, stores it in TimescaleDB,
and layers a device registry, a user-defined rules/alerts engine, pluggable analytics
(asset-state consolidation), and outbound integration (webhooks, SSE streaming)
on top, all behind an async FastAPI service. It's multi-tenant with row-level security and
usage metering, deployed to Azure Container Apps with observability wired to Application
Insights.

**Where it stands today:** the product is well past MVP — the core ingestion → storage →
query → alert → integration path is shipped and in production on Azure, through **Sprint 77**
(between sprints as of this snapshot). Recent work has been operator-experience polish
(Excel-style column filters, asset-binding on reads, configurable fusion strategy). The main
in-flight edges are on the MQTT transport: the broker is still Mosquitto on a single ACI
instance with container-local persistence, plaintext `:1883` is still open alongside the new
`:8883` TLS listener, and mutual-TLS remains deferred.

## Diagram

_No dedicated snapshot diagram yet. The authoritative system overview lives in
[docs/architecture.md](architecture.md); the Azure-specific layout is in
[docs/azure-architecture.md](azure-architecture.md). Add a boxes-and-arrows source under
`docs/diagrams/` (Mermaid/drawio/excalidraw) when a snapshot-level view is warranted._

## Current state

One line per area, each linking to the doc that owns the detail. (The prose
summary above already gives the picture; these are drill-down pointers.)

- **Ingestion** — dual path: MQTT subscriber + HTTP push, both live. See [docs/architecture.md](architecture.md).
- **Storage** — TimescaleDB on Azure PG Flexible Server (PG15): hypertable for tag reads + relational device registry. See [docs/architecture.md](architecture.md).
- **Device registry & config** — CRUD, per-device profiles, status/last-seen tracking. See [docs/data-models.md](data-models.md).
- **Multi-tenancy** — tenant model, `tenant_id` on all tables, row-level security, usage metering. See [ADR-008](adr/008-multi-tenancy-strategy.md).
- **Rules & alerts** — user-defined rules over telemetry; **webhook** alert routing is live, **email** delivery is currently a placeholder (logs intent, no send yet — `src/tagpulse/rules/delivery.py`). See [docs/architecture.md](architecture.md).
- **Analytics** — pluggable modules incl. per-tenant asset-state consolidation (configurable `fusion_strategy`). See [docs/design/sprint-73-configurable-fusion-strategy.md](design/sprint-73-configurable-fusion-strategy.md).
- **Integration layer** — outbound webhooks, SSE streaming, and dead-letter retry are live; **scheduled data exports are planned, not yet shipped** (roadmap G12). See [docs/architecture.md](architecture.md).
- **Admin UI** — React SPA on Azure Static Web Apps; consumes the OpenAPI contract. See [TagPulse-UI](https://github.com/9owlsboston/TagPulse-UI).
- **Observability** — OpenTelemetry → Application Insights, SLO-aligned metric alerts + KQL workbook. See [docs/azure-architecture.md](azure-architecture.md).
- **Deployment** — Azure Container Apps (api/worker) + ACI Mosquitto broker; CI/CD via GitHub Actions. See [docs/runbooks/azure-first-deploy.md](runbooks/azure-first-deploy.md).

## Future state / vision

Where this is heading — the target the current work is closing the gap toward.

- **Managed, HA MQTT broker** — replace the single-instance Mosquitto-on-ACI broker with a managed, highly-available broker (EMQX) that has a first-class persistence story.
- **TLS-only, mutually-authenticated MQTT** — complete the cutover to `:8883`, retire plaintext `:1883`, and land mutual TLS (`require_certificate`) per [ADR-012](adr/012-mtls-for-mqtt.md).
- **Broaden device types** — generalize telemetry modeling beyond RFID readers to additional device classes.

## Open gaps

The known deltas between current and future state (the remediation backlog).

- **Broker persistence is container-local** — retained messages and persistent subscriptions do not survive an ACI restart (Phase-A trade-off, [ADR-017](adr/017-network-hardening.md)). Mitigated by devices republishing on reconnect; resolved by the managed-broker cutover above.
- **Plaintext `:1883` still open** — the `mosquitto.prod.conf` cutover note originally targeted removal in "Sprint 29", but as of Sprint 77 the plaintext listener is still up alongside `:8883`; removal is effectively **unscheduled**, pending the managed-broker cutover above.
- **Mutual TLS deferred** — the `:8883` listener runs `require_certificate false` (server-side TLS only); mTLS is the [ADR-012](adr/012-mtls-for-mqtt.md) workstream.
- **No snapshot-level diagram** — see the Diagram section; add one under `docs/diagrams/` when warranted.
