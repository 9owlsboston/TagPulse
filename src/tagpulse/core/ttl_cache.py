"""Tiny per-process TTL cache.

Sprint 21 (ADR-015 §5 follow-up). Used for:

* ``latest_telemetry`` blocks embedded on ``GET /assets/{id}`` and
  ``GET /lots/{id}``, where a single ``DISTINCT ON (metric_name)`` over
  the hypertable is cheap but a hot tenant page still benefits from a
  short coalescing window.
* ``tenants.telemetry_subject_kinds`` opt-in lookups, where the
  Sprint 19 unbounded process-local cache had no expiry — flipping the
  opt-in via ``PATCH /tenant/config`` required a worker restart for
  the new value to take effect. The 30-second default TTL means the
  flip propagates within one cycle without coordinated invalidation.

Why not Redis pub/sub: the carry-over from Sprint 19 explicitly
optioned "short TTL or Redis"; short TTL is the simpler, no-new-
infrastructure choice. Re-open the decision when (a) operators report
the 30 s settle time as a problem or (b) a Redis dependency lands
for another reason.

Not thread-safe by design: every consumer runs in a single asyncio
loop per worker. Cross-worker state is intentionally not coordinated;
the TTL is the convergence mechanism.
"""

from __future__ import annotations

import time
from collections.abc import Hashable
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):  # noqa: UP046  # PEP 695 syntax requires Py3.12 runtime; supported minimum is 3.11
    """FIFO-evicting cache with per-instance TTL.

    Semantics:

    * ``get(key)`` returns the stored value if present **and** not
      expired; otherwise ``None``.
    * ``set(key, value)`` upserts the value and resets the per-key
      timestamp; evicts the oldest entry once ``maxsize`` is reached.
    * ``invalidate(key)`` removes a single entry (best-effort; no
      error if the key is absent).
    * ``clear()`` drops every entry — useful for test isolation.

    The eviction order is insertion-order, not LRU; ``get`` does not
    touch insertion order. This matches the existing
    ``_bounded_set`` helper in ``ingestion/service.py`` and keeps the
    semantics predictable.
    """

    __slots__ = ("_ttl", "_maxsize", "_clock", "_data")

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        maxsize: int = 1024,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._clock = time.monotonic
        # value, inserted_at
        self._data: dict[K, tuple[V, float]] = {}

    def get(self, key: K) -> V | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, inserted_at = entry
        if self._clock() - inserted_at >= self._ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: K, value: V) -> None:
        if key not in self._data and len(self._data) >= self._maxsize:
            try:
                oldest = next(iter(self._data))
            except StopIteration:  # pragma: no cover -- defensive
                oldest = None
            if oldest is not None:
                self._data.pop(oldest, None)
        self._data[key] = (value, self._clock())

    def invalidate(self, key: K) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:  # pragma: no cover -- diagnostics only
        return len(self._data)
