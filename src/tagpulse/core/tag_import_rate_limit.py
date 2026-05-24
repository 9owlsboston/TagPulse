"""Per-tenant per-hour counter for ``POST /tags/import`` (Sprint 50 C1).

Implements [ADR-028 OQ 4 resolution](../../../docs/adr/028-tags-as-first-class-entity.md):
"10 imports/hour per tenant, configurable via tenant setting."

This is **deliberately separate** from
:mod:`tagpulse.core.rate_limit` — that module is a per-minute,
per-(tenant, route_class) token bucket. The tag-import cap is

- per-hour (1 hour window, sliding),
- per-(tenant, single endpoint),
- per-tenant configurable via ``tenants.tag_bulk_import_rate_limit``.

A separate primitive keeps the existing route-class limiter simple
and avoids cross-talk: tag imports don't consume the ``admin`` route
quota and vice-versa.

In-process state. Single-replica TagPulse today; when we scale out
(Sprint 60+ horizontal API tier) this swaps for a Redis sliding
window — the call surface (``check_and_record(tenant_id, max_per_hour)``)
is intentionally narrow to make that swap mechanical.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque


class _HourlyLimiter:
    """Thread-safe sliding-window counter, 1-hour window."""

    _WINDOW_SECONDS = 3600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[uuid.UUID, deque[float]] = {}

    def check_and_record(
        self,
        tenant_id: uuid.UUID,
        max_per_hour: int,
        *,
        now: float | None = None,
    ) -> bool:
        """Return ``True`` if the request is allowed and recorded.

        Returns ``False`` (and records nothing) if the tenant has
        already issued ``max_per_hour`` imports in the trailing
        hour. ``max_per_hour <= 0`` always returns ``False``
        (the operator has explicitly turned the endpoint off).
        """
        if max_per_hour <= 0:
            return False
        ts = time.monotonic() if now is None else now
        cutoff = ts - self._WINDOW_SECONDS
        with self._lock:
            bucket = self._events.setdefault(tenant_id, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= max_per_hour:
                return False
            bucket.append(ts)
            return True

    def remaining(
        self,
        tenant_id: uuid.UUID,
        max_per_hour: int,
        *,
        now: float | None = None,
    ) -> int:
        """How many imports the tenant can still issue this hour.

        Diagnostic / future ``Retry-After`` plumbing; not used by
        the route layer in C1.
        """
        if max_per_hour <= 0:
            return 0
        ts = time.monotonic() if now is None else now
        cutoff = ts - self._WINDOW_SECONDS
        with self._lock:
            bucket = self._events.get(tenant_id)
            if bucket is None:
                return max_per_hour
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return max(0, max_per_hour - len(bucket))

    def reset(self) -> None:
        """Drop all recorded events. Test-only."""
        with self._lock:
            self._events.clear()


TAG_IMPORT_LIMITER = _HourlyLimiter()
