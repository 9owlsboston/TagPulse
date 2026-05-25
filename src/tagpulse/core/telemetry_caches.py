"""Process-local TTL caches for the subject-scoped telemetry hot path
(Sprint 21 — closes the Sprint 19 carry-overs in ADR-015 §5).

Two caches live here:

* :data:`SUBJECT_KINDS_CACHE` — coalesces reads of
  ``tenants.telemetry_subject_kinds``. The Sprint 19 implementation
  used an unbounded process-local dict with no TTL; flipping the
  opt-in via ``PATCH /tenant/config`` required restarting every worker
  for the new value to take effect. The 30 s TTL turns that into a
  ≤30 s settle window without a coordination dependency.
  ``PATCH /tenant/config`` additionally calls
  :func:`invalidate_subject_kinds` so the *local* worker sees the
  flip immediately; sibling workers converge via the TTL.

* :data:`LATEST_TELEMETRY_CACHE` — coalesces the
  ``DISTINCT ON (metric_name)`` lookups embedded on
  ``GET /assets/{id}`` and ``GET /lots/{id}``. A 30 s window is short
  enough that operators see the same freshness budget as a typical
  dashboard refresh and long enough to absorb F5-mashing without
  hammering the hypertable.

The TTL was chosen rather than Redis pub/sub because (a) the
deployment story stays single-process-by-default, (b) the eventual-
consistency window is short, (c) the wrong outcome on a mis-flip is
"the operator waits 30 s", not "data loss".
"""

from __future__ import annotations

from uuid import UUID

from tagpulse.core.ttl_cache import TTLCache
from tagpulse.models.schemas import LatestTelemetryEntry

# (tenant_id,) -> tuple of opted-in subject kinds (e.g. ("device", "lot"))
SUBJECT_KINDS_CACHE: TTLCache[UUID, tuple[str, ...]] = TTLCache(ttl_seconds=30.0, maxsize=1024)

# (tenant_id, subject_kind, subject_id) -> latest telemetry per metric
LATEST_TELEMETRY_CACHE: TTLCache[tuple[UUID, str, UUID], list[LatestTelemetryEntry]] = TTLCache(
    ttl_seconds=30.0, maxsize=4096
)


def invalidate_subject_kinds(tenant_id: UUID) -> None:
    """Drop the cached opt-in for a tenant.

    Called by ``PATCH /tenant/config`` so the calling worker sees the
    new ``telemetry_subject_kinds`` immediately. Sibling workers
    converge within :attr:`SUBJECT_KINDS_CACHE`'s TTL.
    """
    SUBJECT_KINDS_CACHE.invalidate(tenant_id)
