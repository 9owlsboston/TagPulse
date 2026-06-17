"""Tests for the shared device online/connection-state freshness helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tagpulse.core.device_status import (
    ONLINE_WINDOW,
    effective_connection_state,
    is_fresh,
)

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


class TestIsFresh:
    def test_none_last_seen_is_not_fresh(self) -> None:
        assert is_fresh(None, NOW) is False

    def test_within_window_is_fresh(self) -> None:
        assert is_fresh(NOW - (ONLINE_WINDOW / 2), NOW) is True

    def test_exactly_at_window_edge_is_not_fresh(self) -> None:
        # Strict ">" — a reading exactly ONLINE_WINDOW old is already stale.
        assert is_fresh(NOW - ONLINE_WINDOW, NOW) is False

    def test_older_than_window_is_not_fresh(self) -> None:
        assert is_fresh(NOW - ONLINE_WINDOW - timedelta(seconds=1), NOW) is False

    def test_defaults_now_to_utc_now(self) -> None:
        # A just-now timestamp is fresh without passing ``now`` explicitly.
        assert is_fresh(datetime.now(UTC)) is True


class TestEffectiveConnectionState:
    def test_online_and_fresh_stays_online(self) -> None:
        assert effective_connection_state("online", NOW - timedelta(minutes=1), NOW) == "online"

    def test_online_but_stale_reads_offline(self) -> None:
        assert effective_connection_state("online", NOW - timedelta(minutes=10), NOW) == "offline"

    def test_online_with_no_last_seen_reads_offline(self) -> None:
        assert effective_connection_state("online", None, NOW) == "offline"

    def test_offline_is_unchanged_even_if_fresh(self) -> None:
        # Only a stored "online" is subject to the freshness downgrade.
        assert effective_connection_state("offline", NOW - timedelta(seconds=1), NOW) == "offline"

    def test_unknown_state_is_passed_through(self) -> None:
        assert effective_connection_state("unknown", None, NOW) == "unknown"
