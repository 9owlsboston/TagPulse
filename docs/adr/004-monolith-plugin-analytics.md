# ADR-004: Monolith-First with Plugin Analytics

**Status:** accepted
**Date:** 2026-04-25

## Context

The platform has a core (ingestion, device registry, storage) and extensible analytics modules built per application need. We need an architecture that allows adding analytics modules without over-engineering the initial system.

## Decision

Start with a **monolith** where analytics modules are internal Python packages following a plugin pattern. Extract to separate services only when a module's scale or deployment cadence demands it.

## Consequences

- **Good:** Single deployable unit — simple CI/CD, debugging, and local development.
- **Good:** Analytics modules share the database connection pool and can access any data without cross-service calls.
- **Good:** Plugin interface (base class + registration) makes adding new modules mechanical.
- **Bad:** All modules share the same process — a CPU-heavy analytics job can starve the ingestion path. Mitigated by offloading to background task workers (e.g., Celery, arq).
- **Bad:** Must be disciplined about module boundaries to enable future extraction.

## Alternatives Considered

- **Microservices from day 1:** Clean separation but premature — adds network overhead, distributed tracing, service mesh complexity before we know which modules need independence.
- **Serverless functions per module:** Good isolation but cold starts, vendor lock-in, and harder local development.
