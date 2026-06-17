"""Device online/connection-state freshness (shared definition).

The stored ``devices.connection_state`` column is set to ``"online"`` by the
ingestion writer on every read, but it is **not** aged back to ``"offline"``
when a device goes quiet — MQTT can miss a disconnect, so the column drifts
(see the dashboard "Readers online" tile, which never trusted the column alone).

This module is the single source of truth for the *effective* online status:
a device counts as online only when its ``connection_state`` says ``online``
**and** its ``last_seen`` is fresh (within :data:`ONLINE_WINDOW`). Both the
dashboard aggregate (``tagpulse.services.dashboard``) and the per-device API
response (``_to_response`` in the devices repository) key off the same window,
so the "Readers" card and the Readers page agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# A device is "online" only if it reported within this window. Kept here as the
# single definition consumed by both the dashboard count and the device read
# path (Sprint 54 design discussion).
ONLINE_WINDOW: timedelta = timedelta(minutes=5)

ONLINE = "online"
OFFLINE = "offline"


def is_fresh(last_seen: datetime | None, now: datetime | None = None) -> bool:
    """True when ``last_seen`` falls within :data:`ONLINE_WINDOW` of ``now``."""
    if last_seen is None:
        return False
    current = now or datetime.now(UTC)
    return last_seen > current - ONLINE_WINDOW


def effective_connection_state(
    connection_state: str,
    last_seen: datetime | None,
    now: datetime | None = None,
) -> str:
    """Resolve the *effective* connection state from freshness.

    A stored ``online`` that is no longer fresh (stale or missing ``last_seen``)
    resolves to ``offline`` — the column drifts because a disconnect can be
    missed, so freshness is authoritative. Any non-``online`` stored value
    (``offline`` / ``unknown`` / …) is returned unchanged.
    """
    if connection_state == ONLINE and not is_fresh(last_seen, now):
        return OFFLINE
    return connection_state
