# Design Document: LLM / SLM Integration Strategy

**Date:** 2026-05-02
**Status:** proposed (strategy / phasing — not a sprint design)
**Related:** [data-models.md](../data-models.md), [admin-ui.md](admin-ui.md), [analytics-module-framework.md](analytics-module-framework.md), [alerts-anomaly-detection.md](alerts-anomaly-detection.md), [observability.md](observability.md), [mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md), [edge-device-contract.md](edge-device-contract.md), [docs/refs/edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md), ADR-004 (monolith + plugin analytics), ADR-008 (multi-tenancy)

> **TL;DR.** Server-side LLM is the default path for TagPulse and earns its keep on operator Q&A, alert summarization, NL → query, and rule authoring. Edge-resident SLMs are deferred — they only become attractive when a customer scenario actively breaks on round-trip latency or disconnection (handheld voice UX on a mobile reader, truly off-grid sites, on-prem-only tenants). This document records the reasoning, the integration surface, and the phasing.

---

## 1. Problem Statement

The platform will accumulate a large, well-structured corpus — `tag_reads`, `device_telemetry`, `alerts`, `audit_logs`, `analytics_results`, plus the asset/zone/manifest model layered on top. Operators, ops managers, and integrators will reasonably ask:

- *"What happened at Dock-3 last night?"*
- *"Summarize this week's cold-chain excursions."*
- *"Why did Forklift-12 trigger 40 alerts?"*
- *"Show me all pallets that left Site-A but never reached Site-B."*
- *"Make a rule that pages me if any reader in Cold-Storage stops reporting for 5 minutes."*

Today these require either custom dashboards, hand-written SQL via Data Explorer, or rule-DSL authoring — all of which gate value on operator skill. An LLM-mediated UX collapses that gate.

We need to decide **where** that intelligence lives — server, edge, or both — and lock the decision into our architecture so we don't accrete ad-hoc integrations.

---

## 2. Non-Goals

- Picking a specific model vendor or hosting provider (separate ADR when Phase 1 starts).
- On-tag ML / sensor-side inference — out of scope; sensor tags are dumb endpoints per [rfid-tag-data-model.md](rfid-tag-data-model.md).
- Replacing the Rules Engine with an LLM. Rules stay deterministic; the LLM *authors* them.
- Replacing the Analytics Module Framework. Analytics modules stay deterministic; the LLM *interprets* their outputs.

---

## 3. Decision: Server-Side LLM is the Default

### 3.1 Why server-side wins on the current and near-term workload

| Axis | Server-side LLM | Edge SLM |
|---|---|---|
| **Data scope** | Full tenant history in TimescaleDB; cross-site queries trivial | Local outbox + recent state only; cannot answer historical or cross-site questions |
| **Hardware reach** | Independent of edge tier — works for the $35 Pi Zero 2 W tenants too | Cuts the bottom 80 % of the hardware menu in [edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md); requires ≥4 GB RAM and ideally NPU/GPU |
| **Update cadence** | One rollout updates every tenant | OTA model push to every device; rollback complexity; permanent version skew with server-side tools |
| **Tool surface** | Lives next to the API and service layer the LLM needs to call | Round-trips back to the API anyway → why not co-locate |
| **Cost shape** | Scales with operator queries (low volume); pooled inference cluster | N idle SBCs each holding a quantized model in RAM 24/7 |
| **Compliance** | One auditable egress point per tenant | Per-device data flow makes audit harder, not easier |
| **Eval / safety iteration** | Standard MLOps loop | Fleet-wide eval with bandwidth + heterogeneous hardware constraints |

The key observation: **PresenceTracker + Outbox already filter raw 30 Hz inventory at the edge** ([edge-device-contract.md](edge-device-contract.md)). The LLM never needs to see firehose data. So the classic motivator for edge ML — "bandwidth/latency forces local inference" — does not apply here.

### 3.2 Why edge SLM is *not* eliminated, just deferred

Three scenarios genuinely need on-device language capability. None are present today:

| Trigger scenario | What edge SLM unlocks | Status |
|---|---|---|
| **Truly disconnected ops** (mining, maritime, rural logistics with hours-late uplink) | Local triage + NL queries on the edge agent's own outbox + last-known state | No customer requirement yet |
| **Voice / handheld UX on a mobile reader** ([mobile-carriers-and-manifests.md](mobile-carriers-and-manifests.md)) | Wake-word + intent ("show last 10 reads on Pallet-7") at human latency; warehouse Wi-Fi is unreliable | Future-state UX; no spec yet |
| **Multimodal fusion at source** (camera + RFID dock-door verification) | Vision SLM on the gateway closes the loop without uploading frames | No vision pipeline planned |

> **On-prem deployments** (defense, healthcare tenants who forbid telemetry leaving site) are sometimes confused with edge SLM. They are **not** — they are server-side LLM hosted *inside the customer's network*. Same architecture, different deployment topology. The integration surface in §4 covers this case unchanged.

### 3.3 Architectural framing

The competitive moat in IoT analytics is **the model of the world** — assets, zones, manifests, rules, events. The LM is a UX layer on top of that model. Server-side keeps the LM glued to the world model where it can use the full state, and keeps tool-calling co-located with the service layer that already enforces tenancy and RBAC.

---

## 4. Integration Surface (server-side)

We treat the LLM as a **constrained tool-using client** of the existing service layer — not a parallel data path. This preserves ADR-004 (no business logic outside services) and ADR-008 (RLS at the data boundary).

### 4.1 New module: `src/tagpulse/ai/`

```
src/tagpulse/ai/
    __init__.py
    client.py              # provider-agnostic LLM client (sync + streaming)
    tools/                 # tool definitions exposed to the model
        __init__.py
        query.py           # NL → parametrized SQL on allow-listed views
        assets.py          # wraps AssetRepository, ZoneRepository
        manifests.py       # wraps carrier manifest service (Sprint 15+)
        alerts.py          # read-only alerts/audit lookups
        rules.py           # NL → DSL draft (Phase 2; never auto-applies)
    sessions.py            # per-tenant chat session storage (TTL'd)
    guardrails.py          # PII redaction, prompt-injection detection
    eval/                  # offline evals + golden datasets
```

### 4.2 Tool-calling discipline

- Every tool is an **async function** that takes `(tenant_id: UUID, **kwargs)` and returns a Pydantic model. No exceptions.
- `tenant_id` is **never** a tool argument the model can set — it is bound from the authenticated session at tool-dispatch time. Same trust boundary as `Depends(get_current_user)`.
- Tools call through existing repositories / services. Zero direct SQL in the tools layer (except `query.py`, which is an allow-listed view + parameter validator — see §4.3).
- Every tool dispatch writes an `audit_logs` row with `event_source='ai'` and the tool name + redacted args.

### 4.3 NL → Query (the highest-leverage tool)

- Model produces a **structured query object** (Pydantic), *not* free SQL.
- Backend translates that object to SQL against an **allow-listed set of views**: `asset_current_location`, `tag_reads`, `device_telemetry`, `alerts`, `audit_logs`, `analytics_results`, plus stock/manifest views from later sprints.
- All queries inherit RLS via `current_setting('app.current_tenant')`.
- Hard caps: `LIMIT 1000`, `time_range ≤ 90 days` by default (configurable per tenant).
- Result rendered both as a table in the UI and summarized in chat.

### 4.4 Authoring tools (Phase 2)

- **NL → Rule DSL:** model proposes a rule; UI shows diff; **operator must confirm** before persisting. No silent rule creation, ever.
- **NL → Zone polygon:** in Sprint 17 (after geofence support lands), "draw a zone covering the loading dock" produces a GeoJSON draft for confirmation.

### 4.5 Summarization endpoints

- `POST /ai/summarize/alert/{id}` — explains an alert in context (the rule that fired, recent reads, related alerts).
- `POST /ai/summarize/incident` — multi-alert summarization over a time window.
- `POST /ai/summarize/shift-report` — "what happened at Site-A between 06:00 and 14:00."

These are **derived** endpoints — they compose existing service calls and add a model call on top. They never bypass the service layer.

### 4.6 UI integration (TagPulse-UI repo)

- New **Ask** panel in the admin UI ([admin-ui.md](admin-ui.md)) — chat surface scoped to the user's current tenant + role.
- "Explain this" button on Alert detail, Asset detail, Manifest detail, Analytics result panels.
- Rule authoring page: NL prompt box that drafts → confirm → persist.

---

## 5. Multi-Tenancy & Safety

- **Tenancy.** `tenant_id` is bound at session creation from the JWT, attached to every tool dispatch, and re-asserted at the SQL boundary via RLS. The model never names the tenant.
- **RBAC.** A tool is only available if the caller has the role to do the underlying action. The model's tool list is filtered per session, so e.g. a viewer cannot even *see* the rule-create tool.
- **Prompt injection.** All retrieved content (audit log messages, device metadata, free-form asset names) passes through `guardrails.sanitize_for_prompt()` before being concatenated into context. Tool-call results from one tool are never blindly fed into another without re-validation against its Pydantic schema.
- **PII / sensitive payloads.** Tag user-memory (per [rfid-tag-data-model.md](rfid-tag-data-model.md) §3.2) and device telemetry payloads pass through a redactor — opt-in per tenant.
- **Auditability.** Every model invocation logs: `(tenant_id, user_id, prompt_hash, tool_calls[], token_usage, latency_ms, model_version)`. Full audit trail per [observability.md](observability.md).
- **Egress control.** Single configurable model endpoint per environment; per-tenant override for on-prem deployments. No tenant data crosses to a different provider without explicit config.

---

## 6. Edge Path (parking lot, not roadmap)

If/when an edge SLM scenario lands, the integration is **additive**, not a redesign:

- The edge agent gains an `ai/` subpackage with the same tool-call discipline (read-only against the local outbox + last-known state).
- "Tools" on the edge are a **strict subset** of server tools — anything that needs cross-site or historical data redirects to the server (or fails closed when offline).
- Hardware floor: industrial gateway tier or above per [edge-hardware-and-rfid-primer.md](../refs/edge-hardware-and-rfid-primer.md) — the SBC tier stays language-model-free.
- Model artifact distribution is an OTA channel separate from agent updates; cadence is monthly, not weekly.

This section is a *parking lot* so we don't paint ourselves into a corner. **No edge SLM work is planned.**

---

## 7. Phasing

| Phase | Sprint target | Scope | Gate |
|---|---|---|---|
| **0 — Prep (now)** | this design + small refactors | Lock the `src/tagpulse/ai/` layout; ensure repositories are usable headless (no FastAPI deps); confirm allow-listed views compile under RLS | This doc accepted |
| **1 — Read-only Q&A** | post-Sprint 17 (after assets, zones, geofences, manifests are in) | NL Data Explorer, summarization endpoints, **Ask** panel in UI; tools = read-only | Eval suite ≥ target on golden dataset; security review |
| **2 — Authoring assist** | post-rules-engine maturity | NL → Rule DSL draft (confirm-required), NL → Zone polygon draft | Operator can create a non-trivial rule via NL in < 60 s without touching DSL |
| **3 — Proactive** | TBD | Auto-summarized incident reports, scheduled shift reports, anomaly explanation attached to alerts | Customer demand signal |
| **4 — Edge SLM** | not planned | See §6 | A real customer with §3.2 trigger scenario |

ADRs land at Phase 1 kickoff: model vendor selection, hosting topology, eval methodology.

---

## 8. Open Questions — deferred to Phase 1 kickoff

All six items below are intentionally **deferred** until Phase 1 work begins. Each will be answered in the Phase 1 ADR(s) when the work is funded; locking them now would force decisions without the inputs (vendor benchmarks, real eval data, customer-tenancy constraints) that should drive them.

1. **Model vendor / hosting.** Constraints when chosen: tool-calling support, streaming, per-tenant data isolation guarantees, on-prem deployment story.
2. **Cost attribution.** Per-tenant token metering for chargeback — likely yes; surface as a column in tenant usage reports.
3. **Caching.** Semantic cache for repeated questions ("yesterday's shift report") — likely yes once usage data exists.
4. **Eval methodology.** Golden dataset construction — synthetic via simulator, recorded from internal dogfood, or both.
5. **Streaming UX.** Server-Sent Events vs WebSocket for the **Ask** panel — tactical decision at Phase 1.
6. **Rate limiting.** Per-user, per-tenant, per-tool. Reuse the existing [integration-export-layer.md](integration-export-layer.md) rate-limit primitives where possible.

---

## 9. Acceptance Criteria (for this strategy doc)

- [ ] Engineering agrees server-side is the default and edge SLM is parking-lot.
- [ ] No new code lands in `src/` referencing model providers until Phase 1 starts.
- [ ] Roadmap reflects Phase 1 as a post-Sprint-17 work item (not yet sprint-scoped).
- [ ] Future design docs that touch operator UX (rules, alerts, analytics) note any LLM affordances they expect to gain in Phase 1/2 so we don't double-design.
