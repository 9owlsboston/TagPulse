# ADR-001: Use Python + FastAPI for Platform Backend

**Status:** accepted
**Date:** 2026-04-25

## Context

TagPulse is an IoT platform that ingests RFID tag reads and sensor data, stores them, and runs analytics modules. We need a backend language and framework that supports async I/O (for concurrent device connections), has strong data/analytics libraries, and enables rapid iteration on analytics modules.

## Decision

Use **Python 3.12** with **FastAPI** for the platform backend.

## Consequences

- **Good:** Mature ecosystem for data processing (pandas, numpy, scikit-learn). FastAPI provides async request handling, auto-generated OpenAPI docs, and strong typing via Pydantic.
- **Good:** Analytics modules can be developed as Python packages and imported directly — no cross-service serialization overhead.
- **Bad:** Lower raw throughput than Go or Rust for pure I/O workloads. Mitigated by async and horizontal scaling.
- **Bad:** GIL limits CPU-bound parallelism. Mitigated by offloading heavy analytics to background workers.

## Alternatives Considered

- **Go:** Better throughput, smaller binaries. But weaker data science ecosystem and slower iteration on analytics.
- **Node.js/TypeScript:** Good async model. But weaker data/analytics libraries compared to Python.
