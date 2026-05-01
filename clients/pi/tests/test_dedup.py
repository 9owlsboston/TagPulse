"""Tests for PresenceTracker (dedup + ENTER/EXIT)."""

from tagpulse_edge.dedup import PresenceTracker, Transition


def test_first_observation_emits_enter() -> None:
    tracker = PresenceTracker(dedup_window_s=5.0, exit_timeout_s=10.0)
    ev = tracker.observe("TAG1", "ant-1", monotonic_s=100.0, signal_strength=-40.0)
    assert ev is not None
    assert ev.transition is Transition.ENTER
    assert ev.signal_strength == -40.0


def test_dedup_within_window() -> None:
    tracker = PresenceTracker(dedup_window_s=5.0, exit_timeout_s=10.0)
    tracker.observe("TAG1", "ant-1", 100.0)
    # Second read 1s later → suppressed.
    assert tracker.observe("TAG1", "ant-1", 101.0) is None
    # Even at the edge of the window — still suppressed.
    assert tracker.observe("TAG1", "ant-1", 104.99) is None


def test_different_antennas_are_independent() -> None:
    tracker = PresenceTracker(dedup_window_s=5.0, exit_timeout_s=10.0)
    a = tracker.observe("TAG1", "ant-1", 100.0)
    b = tracker.observe("TAG1", "ant-2", 100.0)
    assert a is not None and b is not None
    assert a.transition is Transition.ENTER
    assert b.transition is Transition.ENTER


def test_exit_after_timeout() -> None:
    tracker = PresenceTracker(dedup_window_s=2.0, exit_timeout_s=10.0)
    tracker.observe("TAG1", "ant-1", 100.0)
    # No EXIT before timeout.
    assert list(tracker.tick(105.0)) == []
    # EXIT at/after timeout.
    events = list(tracker.tick(110.0))
    assert len(events) == 1
    assert events[0].transition is Transition.EXIT
    assert events[0].tag_id == "TAG1"


def test_re_enter_after_exit() -> None:
    tracker = PresenceTracker(dedup_window_s=2.0, exit_timeout_s=5.0)
    tracker.observe("TAG1", "ant-1", 100.0)
    list(tracker.tick(106.0))  # EXIT
    ev = tracker.observe("TAG1", "ant-1", 110.0)
    assert ev is not None
    assert ev.transition is Transition.ENTER
