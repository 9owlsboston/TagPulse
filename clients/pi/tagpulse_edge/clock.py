"""Time validation utilities.

Edge devices frequently boot with a wrong clock (no RTC on most Pis until NTP
syncs). We must:
  - normalize naive timestamps to UTC,
  - reject events that are unreasonably old (server will reject them anyway),
  - reject events too far in the future (clock skew indicator).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timezone


def to_utc(ts: datetime | None) -> datetime:
    """Coerce any datetime to UTC. ``None`` is treated as 'now'."""
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None:
        # Naive: assume UTC. The agent rejects local-time inputs from old code.
        return ts.replace(tzinfo=UTC)
    if ts.tzinfo is timezone.utc or ts.utcoffset() == UTC.utcoffset(None):
        return ts
    return ts.astimezone(UTC)


@dataclass(frozen=True)
class ClockGuard:
    """Bounds-check timestamps for max-age and max-skew-into-future."""

    max_age_s: float
    max_skew_future_s: float

    def is_acceptable(self, ts: datetime, now: datetime | None = None) -> bool:
        now_utc = to_utc(now)
        ts_utc = to_utc(ts)
        delta_s = (now_utc - ts_utc).total_seconds()
        if delta_s > self.max_age_s:
            return False
        if delta_s < -self.max_skew_future_s:
            return False
        return True
