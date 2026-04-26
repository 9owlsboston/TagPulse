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

## 6. Open Questions

- Should continuous aggregates be used in hot query paths, or reserved for dashboards/reports? (Impacts portability to plain PostgreSQL.)
- When multi-tenancy lands (ADR-008), should tenant-specific DB routing live in the repository layer or in a middleware?
- Should we add Alembic migration tests that verify DDL runs on plain PostgreSQL (without TimescaleDB extension) as a portability gate?

---

## 7. Decision Summary

| Decision | Rationale |
|----------|-----------|
| Keep TimescaleDB | Right trade-off for mixed workload, single engine, free self-hosted |
| Don't adopt InfluxDB now | Two-database overhead not justified at current scale |
| Design for Timescale Cloud migration | Connection-string swap, lowest friction managed path |
| Isolate DB-specific code | Protocols for service layer, implementations in `repositories/` |
| Kafka buffer if needed later | Incremental scaling step, doesn't require engine swap |
