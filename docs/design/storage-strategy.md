# Design Document: Storage Strategy

**Date:** 2026-04-25
**Status:** exploration
**Participants:** Architecture review session
**Related:** [ADR-003 (TimescaleDB)](../adr/003-timescaledb-storage.md)

---

## 1. Problem Statement

TagPulse needs a storage strategy that addresses:

1. **Mixed data shapes** — high-frequency time-series (tag reads, alerts) and relational metadata (devices, rules, integrations, tenants).
2. **Cost efficiency** — compression, retention, and managed-vs-self-hosted trade-offs.
3. **Scalability** — from MVP (dozens of readers) to production (thousands of readers, multi-tenant).
4. **Cloud portability** — self-hosted today, managed cloud later, with minimal migration effort.
5. **Technology agnosticism** — the business logic and API layer should not be coupled to a specific database engine.

---

## 2. Current Decision: TimescaleDB

Accepted in [ADR-003](../adr/003-timescaledb-storage.md). The core rationale is **one engine for both data shapes**:

- **Hypertables** auto-partition `tag_reads` by time — fast range queries without manual partitioning.
- **Compression** — 90-95% reduction on older time-series chunks.
- **Retention policies** — automatic drop of data older than N days, no cron jobs.
- **Continuous aggregates** — materialized rollups (reads/hour/reader) maintained automatically.
- **It's PostgreSQL** — asyncpg, SQLAlchemy, Alembic, RLS, pg_dump all work unchanged.

### Cost Profile

| Deployment | Cost |
|-----------|------|
| Self-hosted (Docker/K8s) | Free (Apache 2.0). Pay for compute + storage only. |
| Timescale Cloud (managed) | ~$0.016/hr per vCPU + storage. Starts ~$25/mo. |
| Plain RDS PostgreSQL | Cheaper managed option, but loses hypertables, compression, continuous aggregates. |

Self-hosted TimescaleDB costs the same as plain PostgreSQL — it's a free extension. The cost question only matters with managed offerings, and Timescale Cloud's compression typically reduces storage costs enough to offset the compute premium.

### Scalability Envelope

| Scale | Reads/sec | Strategy |
|-------|-----------|----------|
| Small (< 1K/sec) | Single node, no tuning | Default |
| Medium (1K–10K/sec) | Single node + PgBouncer, compression, write batching | Straightforward |
| Large (10K–100K/sec) | Multi-node distributed hypertables | Built-in feature |
| Very large (100K+/sec) | Kafka/Redpanda write buffer in front of TimescaleDB | Adds a component |

Single-node TimescaleDB on reasonable hardware (8 vCPU, 32GB RAM, NVMe) handles ~10-20K inserts/sec. For RFID readers (dozens to hundreds of reads/sec per reader), this means thousands of active readers before hitting single-node limits.

---

## 3. Alternative Evaluated: InfluxDB

### Overview

InfluxDB is a purpose-built TSDB. v3 is built on Apache Arrow/DataFusion, uses Parquet for storage, and speaks SQL + InfluxQL.

### Architecture Impact — Two Databases Required

```
With InfluxDB:
  tag_reads, alerts -----> InfluxDB       (time-series)
  devices, rules, etc. --> PostgreSQL     (relational)

With TimescaleDB (current):
  everything ------------> TimescaleDB   (both)
```

### Where InfluxDB Wins

| Dimension | InfluxDB | TimescaleDB |
|-----------|----------|-------------|
| Write throughput | 1M+ points/sec single node | ~10-20K inserts/sec single node |
| Storage efficiency | Columnar (Parquet) | Row-oriented with compression |
| Built-in downsampling | Native tasks | Continuous aggregates (comparable) |
| Cloud offering | Serverless, usage-based | Instance-based |

### Where InfluxDB Loses for TagPulse

| Concern | Impact |
|---------|--------|
| Two databases | Double operational burden: backups, connection pools, migrations, monitoring |
| No cross-store JOINs | Can't `JOIN tag_reads ON devices.id` — must join in application code |
| No foreign keys | Orphaned records must be handled in app logic |
| No cross-store transactions | Can't atomically write device + first tag read |
| No RLS | Tenant isolation via InfluxDB buckets, not row-level security |
| Tooling mismatch | SQLAlchemy, Alembic, asyncpg don't work with InfluxDB |
| Team knowledge | PostgreSQL is ubiquitous; InfluxDB is niche |

### Cost Comparison

| Scale | InfluxDB + PostgreSQL | TimescaleDB (single engine) |
|-------|----------------------|----------------------------|
| Small | ~$40-60/mo (two services) | ~$25/mo |
| Medium | ~$100-150/mo | ~$50-80/mo |
| Large | InfluxDB write efficiency helps, but total is still two services | Single engine scales linearly |

### When InfluxDB Would Make Sense

Only if **all three** conditions are true:

1. Write volume exceeds 50K+ reads/sec sustained (thousands of active readers).
2. Query patterns are almost entirely time-range scans (no complex JOINs with device metadata).
3. The team is willing to operate two database engines.

### Conclusion

For TagPulse's workload (mixed relational + time-series, small team, hundreds of readers), TimescaleDB is the better fit. If write throughput becomes a bottleneck, adding Kafka as a write buffer is a smaller change than swapping the storage engine.

---

## 4. Migration: Self-Hosted → Managed Cloud

### Option A: Timescale Cloud (lowest friction)

| Step | Effort |
|------|--------|
| Provision instance | Minutes |
| `pg_dump` / `pg_restore` (or `timescaledb-parallel-copy` for large datasets) | Hours depending on data size |
| Update `DATABASE_URL` env var | One-line change |
| Re-create compression policies and continuous aggregates (not preserved by pg_dump) | Scripted |
| Cut over | Minutes |

**No code changes.** Same engine, same extension, same SQL.

### Option B: AWS RDS / Azure Database for PostgreSQL

Loses TimescaleDB-specific features:

| Feature | TimescaleDB | Plain PostgreSQL | Migration work |
|---------|------------|------------------|----------------|
| Hypertables | Automatic partitioning | Manual `PARTITION BY RANGE` DDL | Rewrite migrations |
| Compression | Built-in, 90-95% | TOAST only (much less effective) | Accept higher storage cost or add pg_lz4 |
| Retention | Built-in policies | pg_cron + manual partition drops | Write cron job |
| Continuous aggregates | Built-in | Materialized views + manual refresh | Rewrite refresh logic |

**Effort:** 1-2 sprints. App code (queries) mostly works, but DDL and background policies need rewriting.

### Option C: Self-Host on Cloud K8s (EKS/AKS/GKE)

Same Docker image, same TimescaleDB extension. You manage Kubernetes; no feature loss.

### Recommendation

Design for Timescale Cloud as the primary managed path (connection-string swap). Keep TimescaleDB-specific DDL isolated in migrations and a single `policies.py` module to make Option B feasible if forced by cloud provider mandates.

---

## 5. Storage Layer Abstraction

### Layered Architecture

```
┌──────────────────────────────────────────────────┐
│  API / Ingestion Layer                           │
│  Knows: Pydantic schemas, service interfaces     │
├──────────────────────────────────────────────────┤
│  Service Layer                                   │
│  Knows: repository protocols                     │
├──────────────────────────────────────────────────┤
│  Repository Protocols (Python Protocol classes)  │
│  Technology-agnostic contracts                   │
├──────────────────────────────────────────────────┤
│  Repository Implementations                      │
│  TimescaleDB/SQLAlchemy (default)                │
│  InfluxDB + PostgreSQL (alternative)             │
│  In-memory (unit tests)                          │
└──────────────────────────────────────────────────┘
```

### Protocol Example

```python
# src/tagpulse/repositories/protocols.py
class TagReadRepository(Protocol):
    async def insert(self, tenant_id: UUID, read: TagReadCreate) -> TagRead: ...
    async def insert_batch(self, tenant_id: UUID, reads: list[TagReadCreate]) -> int: ...
    async def query(
        self, tenant_id: UUID, *,
        reader_id: UUID | None = None,
        tag_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagRead]: ...

class DeviceRepository(Protocol):
    async def create(self, tenant_id: UUID, device: DeviceCreate) -> Device: ...
    async def get(self, tenant_id: UUID, device_id: UUID) -> Device | None: ...
    async def list(self, tenant_id: UUID, ...) -> list[Device]: ...
    async def update(self, tenant_id: UUID, device_id: UUID, patch: DeviceUpdate) -> Device: ...
    async def delete(self, tenant_id: UUID, device_id: UUID) -> None: ...
```

### Technology Boundary Map

| Layer | Tech-agnostic? | Reason |
|-------|---------------|--------|
| Pydantic schemas | Yes | Pure data classes |
| Repository protocols | Yes | Python `Protocol`, no DB imports |
| Service functions | Yes | Depend only on protocols |
| API routes | Yes | Depend on protocols via `Depends()` |
| Repository implementations | No (by design) | SQLAlchemy, InfluxDB client, etc. live here |
| Alembic migrations | No | DDL is engine-specific |
| DB policies (compression, retention) | No | TimescaleDB-specific, isolated in one module |

### Project Structure

```
src/tagpulse/
  repositories/
    protocols.py              # Technology-agnostic contracts
    timescaledb/              # Default implementation
      tag_reads.py
      devices.py
      rules.py
      session.py              # AsyncSession factory, engine setup
      policies.py             # Compression, retention (TimescaleDB-specific)
```

### What Swapping Engines Requires

| Swap to... | Write | Don't touch |
|-----------|-------|-------------|
| InfluxDB + PostgreSQL | New `repositories/influxdb/` package | Services, routes, schemas, tests |
| Plain PostgreSQL | New `repositories/postgres/` without hypertable DDL | Same |
| DynamoDB + Timestream | New `repositories/aws/` package | Same |

### Design Principles

1. **Protocols are the abstraction.** No generic "database adapter" framework needed.
2. **TimescaleDB-specific DDL stays in migrations + `policies.py`.** One module to swap.
3. **Unit tests use in-memory fakes.** Integration tests hit real TimescaleDB.
4. **Don't over-abstract on day one.** Protocols + one implementation + `Depends()` wiring is sufficient.

---

## 6. Decisions & Open Questions

### Resolved

| # | Question | Decision |
|---|---|---|
| 3 | Migration tests on plain PostgreSQL as portability gate? | **Yes.** Add an Alembic migration test that runs DDL against plain PostgreSQL (no TimescaleDB extension) in CI. Catches accidental Timescale-only DDL early and protects the "swap connection string to Timescale Cloud" migration story. |
| 1 | Continuous aggregates in hot paths or dashboards-only? | **Hot paths via a deterministic `MetricsRepository` abstraction (Option B-deterministic).** Both backends are first-class — not a fallback model. Single seam: `MetricsRepository` is the only place backend-specific aggregation lives, selected once at startup from `DATABASE_BACKEND` config (`timescale` \| `postgres`). **Timescale impl** uses continuous aggregates (`tag_reads_hourly_by_reader`, `alerts_daily_by_tenant`, etc.); **PG impl** uses materialized views refreshed by `pg_cron` (or an app-side scheduler) on the same buckets. Both ship in v1 with CI integration tests. **Scope of the abstraction is intentionally tight:** only time-bucketed aggregation queries go through `MetricsRepository` (estimated 4–8 methods over the platform's lifetime). Single-row lookups, simple filters, and `LIMIT` queries stay on regular repositories with plain SQL that runs identically on both backends. **Product positioning:** PG mode has an explicit scaling ceiling (benchmarked — see [§6.1 PG-mode scaling ceiling](#61-pg-mode-scaling-ceiling)) past which TimescaleDB is required. **Review rule:** any new method on `MetricsRepository` requires both implementations in the same PR. |
| 2 | Tenant DB routing: repository layer or middleware? | **Hybrid — middleware default, explicit override for non-request code (Option C), with mixed-tier capability built in from v1.** Single seam: `db_session_var: ContextVar[AsyncSession]` in `tagpulse.core.context`. **Per-request path:** middleware resolves the tenant, looks up `tenants.db_pool_key`, fetches a session from the matching pool in a startup-built `PoolRegistry`, sets `app.current_tenant_id` for shared-pool tenants, binds the contextvar, runs the request, resets on exit. **Background / admin path:** `async with tenant_context(tenant_id):` binds the contextvar manually for non-request code (rules engine, scheduled jobs, scripts). **Cross-tenant operations** go through a dedicated `AdminRepository` that takes an explicit `tenant_id` and is gated by an admin role at the route layer — visible in code review. **Mixed-tier deployment** is supported by the same mechanism: most tenants share `db_pool_key='shared_default'` with RLS isolation; tenants with sovereignty / residency requirements get their own `db_pool_key` pointing at a dedicated cluster in the required region. Promoting a tenant from shared to dedicated is a `pg_dump`-filtered-by-`tenant_id` data move + one row update on `tenants.db_pool_key`; **no code change**. **v1 scope (no Tier-2 customer yet):** add `tenants.db_pool_key VARCHAR(64) NOT NULL DEFAULT 'shared_default'`; introduce `db_session_var` and `tenant_context()`; wire existing `get_session()` dependency to populate the contextvar. Pool registry initially has one entry (`shared_default`). Tier-2 onboarding becomes a configuration task, not a refactor. |

### 6.1 PG-mode scaling ceiling

Source of truth: [`scripts/benchmark_pg_metrics.py`](../../scripts/benchmark_pg_metrics.py). Reproduce with `docker compose up -d db && python scripts/benchmark_pg_metrics.py`.

**Workload**: 24 h of `tag_reads`, 60 reads/h/device, single tenant, hourly aggregation grouped by `reader_id`. Numbers are floor-of-floor — dev hardware running `timescale/timescaledb:latest-pg16` in Docker with default config (no `shared_buffers` tuning, no parallel workers configured, no `pg_cron`). Expect 3–10× headroom on tuned ops hardware.

| Fleet (devices) | Rows | Cold p50 / p95 / p99 (ms) | Matview refresh (ms) | Matview p50 / p95 / p99 (ms) |
|---:|---:|---|---:|---|
|   100 |   144,000 |  128.7 /  153.0 /  155.4 |    67 |   4.3 /  25.1 /  30.3 |
|   500 |   720,000 |  937.4 / 1144.4 / 1154.9 |   329 |  29.4 /  67.3 /  73.6 |
| 1,000 | 1,440,000 | 2345.2 / 2615.7 / 2621.3 |   593 |  69.4 /  84.6 /  86.7 |
| 2,000 | 2,880,000 | 5020.7 / 5928.3 / 6041.9 |  1705 | 168.3 / 205.2 / 206.3 |
| 5,000 | 7,200,000 | 5092.5 / 6166.2 / 6179.1 |  5824 | 593.3 / 692.0 / 701.7 |

**Findings**:

* **Cold raw-table path** crosses the 1 s sub-second-dashboard target between **100 and 500 devices/tenant**. Without a matview, PG mode is dashboard-viable only for the smallest tenants.
* **Matview path** stays well under 1 s through 2k devices and reaches ~700 ms p99 at 5k devices. **The matview is the load-bearing piece** — the design's "dashboards-only" PG strategy is contingent on the matview actually being refreshed.
* **Matview refresh cost grows roughly linearly** with row count (67 ms → 5.8 s for 100 → 5,000 devices). At 5k devices a full refresh is too expensive for a 1 min `pg_cron` cadence; **PG-mode tenants above ~2k devices need either incremental matviews or TimescaleDB continuous aggregates** (which is exactly the migration path the abstraction was designed for).
* **Operational ceiling for PG mode (dev hardware, conservative)**: **~2,000 devices/tenant for 60-second dashboard freshness**. Tuned ops hardware should push that to **~5,000–10,000 devices/tenant** before the matview refresh window becomes the bottleneck. Past that point, TimescaleDB continuous aggregates (incremental, no full rebuild) are required — `DATABASE_BACKEND=timescale` is a config change, not a code change, per the abstraction's design contract.

This number is intentionally a *floor*. Customer-facing capacity statements should re-run the harness on the actual deployment hardware before quoting a ceiling.

---

## 7. Decision Summary

| Decision | Rationale |
|----------|-----------|
| Keep TimescaleDB | Right trade-off for mixed workload, single engine, free self-hosted |
| Don't adopt InfluxDB now | Two-database overhead not justified at current scale |
| Design for Timescale Cloud migration | Connection-string swap, lowest friction managed path |
| Isolate DB-specific code | Protocols for service layer, implementations in `repositories/` |
| Kafka buffer if needed later | Incremental scaling step, doesn't require engine swap |
