# ADR-003: Use TimescaleDB for Storage

**Status:** accepted
**Date:** 2026-04-25

## Context

The platform needs to store two categories of data:
1. **Time-series telemetry** — RFID tag reads (tag ID, reader ID, timestamp, signal strength) arriving at high frequency.
2. **Relational metadata** — device registry, user accounts, analytics module configuration.

We want a single database engine that handles both patterns to reduce operational complexity in v1.

## Decision

Use **TimescaleDB** (PostgreSQL extension) as the primary data store.

## Consequences

- **Good:** Time-series hypertables for tag reads with automatic partitioning, compression, and retention policies.
- **Good:** Full PostgreSQL for relational data (device registry, users, config) in the same database.
- **Good:** Standard SQL — no proprietary query language. Works with existing Python libraries (asyncpg, SQLAlchemy).
- **Good:** Continuous aggregates for pre-computed rollups (reads per hour, per reader, etc.).
- **Bad:** Single-node write throughput has limits. At very high scale (100K+ reads/sec), may need sharding or a dedicated streaming layer in front.
- **Bad:** Heavier than a pure key-value store if we only needed simple lookups.

## Alternatives Considered

- **InfluxDB:** Purpose-built TSDB, excellent write performance. But requires a separate PostgreSQL for relational data — two systems to manage.
- **PostgreSQL (plain):** Works but lacks time-series optimizations (partitioning, compression, retention policies out of the box).
