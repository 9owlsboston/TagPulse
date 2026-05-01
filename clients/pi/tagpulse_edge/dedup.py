"""De-duplication and ENTER/EXIT state machine.

Pure logic — no I/O, no threads, fully unit-testable. The agent feeds raw
reads in (with a monotonic time source) and consumes the resulting stream
of ENTER / EXIT events.

Contract (see design doc §A5):
  - dedup_window_s: identical (tag_id, antenna) reads inside this window
    collapse to one observation.
  - exit_timeout_s: a tag is considered "gone" after no observation for
    this long; on transition we emit EXIT.
  - First observation of a (tag_id, antenna) emits ENTER.

The state machine is deliberately keyed on `(tag_id, antenna)`, not just
`tag_id`: the same tag may legitimately be read by multiple antennas (zone
transitions) and each is its own presence.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class Transition(str, Enum):
    ENTER = "ENTER"
    EXIT = "EXIT"


@dataclass(frozen=True)
class PresenceEvent:
    tag_id: str
    antenna: str
    transition: Transition
    monotonic_s: float
    """Monotonic seconds when the transition was decided."""

    signal_strength: float | None = None
    """For ENTER: signal of the read that triggered ENTER. For EXIT: None."""


@dataclass
class _Presence:
    last_seen_mono: float
    last_signal: float | None


class PresenceTracker:
    """Stateful tracker for ENTER/EXIT and dedup.

    Use ``observe(...)`` for each raw read and ``tick(...)`` periodically (at
    least every ``exit_timeout_s / 2``) to flush EXIT events for absent tags.
    """

    def __init__(self, dedup_window_s: float, exit_timeout_s: float) -> None:
        if dedup_window_s < 0:
            raise ValueError("dedup_window_s must be >= 0")
        if exit_timeout_s <= 0:
            raise ValueError("exit_timeout_s must be > 0")
        self._dedup_window_s = dedup_window_s
        self._exit_timeout_s = exit_timeout_s
        self._present: dict[tuple[str, str], _Presence] = {}

    # -- Inputs --

    def observe(
        self,
        tag_id: str,
        antenna: str,
        monotonic_s: float,
        signal_strength: float | None = None,
    ) -> PresenceEvent | None:
        """Register one raw read. Returns an ENTER event, or None if dedup'd."""
        key = (tag_id, antenna)
        existing = self._present.get(key)
        if existing is None:
            self._present[key] = _Presence(monotonic_s, signal_strength)
            return PresenceEvent(
                tag_id=tag_id,
                antenna=antenna,
                transition=Transition.ENTER,
                monotonic_s=monotonic_s,
                signal_strength=signal_strength,
            )
        # Already present: dedup window suppresses, but refresh last_seen.
        if monotonic_s - existing.last_seen_mono >= self._dedup_window_s:
            existing.last_seen_mono = monotonic_s
            existing.last_signal = signal_strength
        return None

    def tick(self, monotonic_s: float) -> Iterable[PresenceEvent]:
        """Emit EXIT events for tags absent longer than ``exit_timeout_s``."""
        expired = [
            key
            for key, p in self._present.items()
            if monotonic_s - p.last_seen_mono >= self._exit_timeout_s
        ]
        for key in expired:
            del self._present[key]
            tag_id, antenna = key
            yield PresenceEvent(
                tag_id=tag_id,
                antenna=antenna,
                transition=Transition.EXIT,
                monotonic_s=monotonic_s,
                signal_strength=None,
            )

    # -- Introspection (used by status / heartbeat) --

    def present_count(self) -> int:
        return len(self._present)
