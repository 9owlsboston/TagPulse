"""Tests for clock validation."""

from datetime import UTC, datetime, timedelta, timezone

from tagpulse_edge.clock import ClockGuard, to_utc


def test_to_utc_handles_naive_and_aware() -> None:
    naive = datetime(2026, 5, 1, 12, 0, 0)
    assert to_utc(naive).tzinfo is UTC
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 5, 1, 7, 0, 0, tzinfo=eastern)
    assert to_utc(aware).hour == 12


def test_clock_guard_accepts_recent() -> None:
    guard = ClockGuard(max_age_s=3600, max_skew_future_s=60)
    now = datetime.now(UTC)
    assert guard.is_acceptable(now - timedelta(seconds=10), now=now)


def test_clock_guard_rejects_too_old() -> None:
    guard = ClockGuard(max_age_s=60, max_skew_future_s=60)
    now = datetime.now(UTC)
    assert not guard.is_acceptable(now - timedelta(seconds=120), now=now)


def test_clock_guard_rejects_too_far_in_future() -> None:
    guard = ClockGuard(max_age_s=60, max_skew_future_s=30)
    now = datetime.now(UTC)
    assert not guard.is_acceptable(now + timedelta(seconds=120), now=now)
