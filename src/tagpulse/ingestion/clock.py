"""Sprint 16 — ingestion clock-window enforcement.

Per [docs/design/edge-device-contract.md §3.5](../../../docs/design/edge-device-contract.md):

- Reject events with ``timestamp < now − 24 h`` (reason ``event_too_old``).
- Reject events with ``timestamp > now + 5 min`` (reason ``event_in_future``).

Rejections are logged, dead-lettered, and metered by the caller; this module
is a pure stateless helper to keep the rule trivially testable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Max acceptable lag between event ``timestamp`` and server ``now``.
MAX_PAST = timedelta(hours=24)
#: Max acceptable lead between event ``timestamp`` and server ``now``.
MAX_FUTURE = timedelta(minutes=5)

REASON_TOO_OLD = "event_too_old"
REASON_IN_FUTURE = "event_in_future"


class ClockRejectionError(Exception):
    """Raised by ingestion when an event timestamp falls outside the contract
    clock window. The route layer translates this to HTTP 400."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_clock_window(
    timestamp: datetime, now: datetime | None = None
) -> str | None:
    """Return a rejection reason string, or ``None`` if the timestamp is in window.

    A naive timestamp is interpreted as UTC (matches the contract: devices
    MUST publish UTC-aware values, but defensive callers may pass naive).
    """
    if now is None:
        now = datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    skew = timestamp - now
    if skew < -MAX_PAST:
        return REASON_TOO_OLD
    if skew > MAX_FUTURE:
        return REASON_IN_FUTURE
    return None
