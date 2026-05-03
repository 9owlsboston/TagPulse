"""Conformance: clock window — docs/design/edge-device-contract.md §3.5."""

from datetime import timedelta

from tagpulse.ingestion.clock import (
    MAX_FUTURE,
    MAX_PAST,
    REASON_IN_FUTURE,
    REASON_TOO_OLD,
)


def test_max_past_is_24h() -> None:
    """§3.5 — `timestamp < now − 24h` ⇒ `event_too_old`."""
    assert timedelta(hours=24) == MAX_PAST


def test_max_future_is_5min() -> None:
    """§3.5 — `timestamp > now + 5min` ⇒ `event_in_future`."""
    assert timedelta(minutes=5) == MAX_FUTURE


def test_rejection_reason_strings() -> None:
    """§3.5 — rejection reasons are the spec'd identifiers."""
    assert REASON_TOO_OLD == "event_too_old"
    assert REASON_IN_FUTURE == "event_in_future"
