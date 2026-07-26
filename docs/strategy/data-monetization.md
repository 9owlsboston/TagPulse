# Strategy: data monetization — privacy-preserving cross-tenant value

**Date:** 2026-07-26
**Status:** exploration
**Related:** [ADR-008 (multi-tenancy)](../adr/008-multi-tenancy-strategy.md), [ai-landscape.md](ai-landscape.md), [edge-ai-architecture.md](edge-ai-architecture.md), [competitive-positioning.md](competitive-positioning.md)

---

## Summary

A multi-tenant telemetry platform quietly accumulates a **cross-customer data asset**. The prize
is turning that into a product — **benchmarks, indices, and better models** — *without* breaching
tenant isolation or trust. The hard constraint: per-tenant RLS
([ADR-008](../adr/008-multi-tenancy-strategy.md)) protects *point queries*, but any cross-tenant
aggregation is a **new data path** that must enforce minimum-cohort size, anonymization, and
consent. Done right it drives stickiness and a premium tier; done wrong it torches customer
trust. Market framing here is `unverified`.

## The asset and the monetization patterns

| Pattern | What it is | Example |
|---|---|---|
| **Benchmarking / percentiles** | "you vs your cohort" on an aggregate metric | "your reefer excursion rate is P80 for cold-chain in your region" |
| **Derived indices / market signals** | anonymized, aggregated trends sold as a signal | regional dwell/throughput index, cold-chain excursion index |
| **Model-as-asset** | cross-tenant-trained models sold back as better analytics | a fleet-wide predictive-maintenance model beats any single tenant's data |
| **Data clean rooms** | partners query aggregates under governance, never raw rows | an insurer analyzes loss-prevention efficacy across a book |

Benchmarking is the **safest, highest-trust entry point**: customers *want* to know how they
compare, and it's a natural **premium analytics tier** metered through the existing usage system.

## Prior art in-house: the ACR benchmark pattern

The team's own analytics practice already models this: privacy-preserving **industry / segment
benchmarks** that report a customer against a cohort using *aggregates and volume percentiles*,
never another customer's raw figures. That same discipline — cohort-level rollups, minimum group
size, "characterize by aggregate, never by an identifiable row" — is the template for a
telemetry benchmark product.

## Guardrails (non-negotiable)

- **Minimum cohort size / k-anonymity** — never emit a benchmark computed over too few tenants;
  a P50 across 2 competitors is a leak.
- **Opt-in consent + purpose limitation** — cross-tenant use is a *different purpose* than
  running the service; get explicit consent (GDPR), and use the data only for what was consented.
- **Anonymization + aggregation at the source** — the cross-tenant path emits rollups, not rows;
  differential-privacy noise for small-N metrics.
- **Competitive-sensitivity awareness** — tenants in the same market must not be able to
  re-identify each other from a benchmark; suppress narrow slices.
- **A separate, audited data path** — do **not** relax RLS to build this; add a governed
  aggregation service with its own access controls and audit trail (`audit_logs`).

This aligns with the ecosystem's standing privacy rule: only scrubbed, account-agnostic
aggregates ever cross a tenant boundary.

## AI angle

- **Federated learning** — train a shared model across tenants **without pooling raw data**
  (ties to [edge-ai-architecture.md](edge-ai-architecture.md)); the model improves, the data
  never leaves the tenant.
- **Cohort anomaly detection** — "your device behaves unlike its peers" is more powerful than a
  single-tenant baseline.
- **The flywheel** — more tenants → better benchmarks & models → stronger product → more
  tenants. This is the compounding advantage a horizontal toolkit
  ([competitive-positioning.md](competitive-positioning.md)) can't easily copy.

## Risks

- **Trust is the whole game** — customers hate discovering their data was reused; **transparency +
  opt-in beats extraction** every time.
- **Regulatory** — GDPR/CCPA, sector rules (health, insurance), data-residency; consent and
  purpose limitation are legal requirements, not niceties.
- **Re-identification** — naive aggregation can leak; small-N slices and outliers are the danger.
- **Sequencing** — this is a **later-stage** play; it needs tenant density to produce useful
  cohorts and a mature trust/consent posture first. Premature monetization poisons adoption.
