# Strategy: AI landscape — where this prototype can play

**Date:** 2026-07-26
**Status:** exploration
**Related:** [architecture.md](../architecture.md), [ADR-004 (plugin analytics)](../adr/004-monolith-plugin-analytics.md), [ADR-010 (internal event bus)](../adr/010-internal-event-bus.md), [sensor-wedges.md](sensor-wedges.md)

---

## Summary

TagPulse is an unusually **AI-ready substrate** for a product that was scoped as an IoT
platform. The streaming EventBus, the subject-scoped TimescaleDB store, the pluggable
analytics-module framework, the moving cellular edge gateway, and the OpenAPI contract are each
a seam that AI plugs into with little friction. This note maps those assets to **four plug-in
patterns** and the vertical products they unlock, then recommends a first build.

Market and TAM statements below are `unverified` — they are directional, not researched.

## The AI-relevant assets (grounded in the repos)

| Asset | Where | Why it matters for AI |
|---|---|---|
| Real-time multi-modal telemetry pipeline | `ingestion/` + `events/` EventBus (asyncio → Redis Streams → Kafka roadmap, [ADR-010](../adr/010-internal-event-bus.md)) | A **streaming feature pipeline**; ML inference is just another subscriber. |
| Subject-scoped time-series store | TimescaleDB `telemetry_readings` keyed on `(tenant, subject_kind, subject_id, metric, ts)` | Labeled, tenant-isolated **training + feature substrate**. |
| Pluggable analytics-module framework | [ADR-004](../adr/004-monolith-plugin-analytics.md), `analytics/`; first module = read-freq + anomaly flag | **The drop-in slot for ML models** — the anomaly hook already exists (rules-based today). |
| Deterministic rules/alerts engine | `rules/` (threshold / absence / rate-change) | Upgrade path to **learned baselines & predictive alerts**. |
| Moving edge gateway | [TagPulse-Mobile](https://github.com/9owlsboston/TagPulse-Mobile) `:gateway-core` + `:obdii`, `GatewayDriver` seam | The **edge-AI frontier**: sensor fusion + on-device inference + cellular uplink. |
| Vehicle telematics data | `:obdii` ELM327 driver, VIN-bound reads | Wedge into **connected-vehicle / fleet AI**. |
| Geospatial layer | `geo/` polygon geofencing, zones, dwell tracking | **Spatiotemporal ML** (ETA, dwell prediction, route anomaly). |
| Multi-tenant + metered + RLS | [ADR-008](../adr/008-multi-tenancy-strategy.md) | AI features are **productizable & billable** out of the box. |
| OpenAPI contract | `openapi.json` | **Instant agent/MCP tool surface.** |
| Azure-native hosting | ACA + App Insights + OTel | Drops into **Azure AI Foundry / ML / OpenAI**. |

## Four ways AI plugs in (mapped to existing seams)

### 1. AI *on* the data — analytics/inference layer (lowest friction)

The analytics-module framework ([ADR-004](../adr/004-monolith-plugin-analytics.md)) was
practically designed for this. Ship models as modules that subscribe to `tag_read.created` /
`telemetry.out_of_range`, write to `analytics_results`, and fan alerts back through the
existing rules → integration path.

- **Predictive maintenance** on OBD-II PIDs → remaining-useful-life / fault prediction per VIN.
- **Learned anomaly detection** replacing static thresholds — per-subject seasonal baselines.
- **Forecasting** — inventory demand, dwell-time, transit ETA (transit legs already modelled,
  Sprint 72).
- **ML sensor fusion** — the configurable `fusion_strategy` (Sprint 73) is a natural home for a
  learned strategy.
- **Driver-behavior / safety scoring** — accelerometer + OBD → harsh-event detection (UBI).

### 2. AI *at* the edge — the gateway is the star (biggest differentiator)

The `GatewayDriver` seam + durable outbox is where a small on-device model belongs. Cellular
uplink makes edge inference a **cost lever**, not just a latency one.

- **Pre-filter/classify before uplink** — TinyML drops boring frames → bandwidth savings.
- **Offline anomaly detection** — the gateway alerts even when disconnected (outbox survives
  restart).
- **Vision at the edge** — ML Kit already does barcode/VIN; extend to **visual inspection**
  (damage/defect, plate OCR, gauge reading).
- **Local context classification** — GPS+accel+OBD fusion → idling / driving / loading.

### 3. AI *as* the interface — agentic/LLM layer (fastest "wow")

`openapi.json` makes this almost free: wrap it as an **MCP server / tool schema** and an LLM
can query and configure the platform.

- **NL telemetry query** — "where was forklift 7 yesterday 2–4pm?" → query API.
- **Rule-authoring copilot** — natural language → a rule (the rules DSL is complex enough that
  this is real value).
- **Alert-storm triage/summarization** — LLM digests alert history + [runbooks](../runbooks/)
  into incident narratives (RAG over ops data).
- **Conversational analytics** embedded in the admin UI.

### 4. AI-ready data platform — the substrate play (longest horizon)

- Add a **vector store** (pgvector on the same PG, or Azure AI Search) for semantic search +
  RAG over events/assets/runbooks.
- **Feature-store** semantics on the EventBus (Phase-2 Redis Streams / Phase-3 Kafka).
- **Model serving** as another integration consumer; outputs pushed via existing webhooks/SSE.

## Vertical products this unlocks (`unverified` market sizing)

- **Connected-vehicle / fleet telematics AI** — predictive maintenance, UBI insurance scoring,
  fuel/emissions optimization. (OBD-II is the wedge.)
- **Cold-chain / pharma** — reefer monitoring + predictive excursion alerts.
- **Supply-chain visibility** — ETA prediction, theft/shrinkage anomaly.
- **Warehouse RTLS optimization** — congestion/throughput models on reader+zone data.

## Recommended first build (highest signal, lowest risk)

1. **LLM query + rule-authoring copilot over `openapi.json` (MCP server).** Days, not weeks; no
   data science; immediately demoable; leverages the contract already maintained.
2. **One real ML analytics module** (predictive-maintenance or learned-anomaly) shipped through
   the existing module → rules → integration path — proves the substrate is ML-ready E2E.
3. **Edge pre-filter model** on the gateway — the true differentiator; start with a tiny
   on-device anomaly/vision classifier behind the `GatewayDriver` seam.

## Gaps / risks to name up front

- **No GPU in the current ACA footprint** — heavy training/serving needs Azure ML/Foundry;
  inference-only modules are fine on CPU.
- **TimescaleDB is not a vector DB** — add pgvector or Azure AI Search for semantic/RAG.
- **Edge footprint budget is a hard constraint** ([TagPulse-Mobile AGENTS.md](https://github.com/9owlsboston/TagPulse-Mobile)) — models must be quantized/tiny.
- **Multi-tenant model isolation** — per-tenant vs shared models is a design + privacy decision;
  RLS covers data, not model leakage.
- **Labeling** — supervised cases need labeled data; the sim foundation (Sprints 58–59) helps
  bootstrap, real labels don't exist yet.
- **mTLS/broker hardening still open** ([ADR-012](../adr/012-mtls-for-mqtt.md)) — tighten the
  edge trust boundary before autonomous edge inference in production.
