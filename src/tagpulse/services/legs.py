"""Transit-leg environment + cold-chain SLA summary (Sprint 72, ADR-034 Phase 2).

Pure, I/O-free scoring of a leg's fused environment series (from
``asset_state_history`` over ``[departed_at, arrived_at]``) against the optional
per-tenant :class:`tagpulse.services.consolidation.SlaConfig`. The
``AssetLegTracker`` calls :func:`summarize_leg_env` when it closes a leg and
persists the result onto the ``asset_legs`` row.

Envelope (min/max/mean temp, min/max humidity) is always computed from the
samples. SLA scoring (``excursion_s``, ``in_range_pct``, ``sla_breached``) is
computed only when an :class:`SlaConfig` is supplied — absent SLA → those stay
``None`` (envelope-only).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from tagpulse.services.consolidation import SlaConfig

__all__ = ["EnvSample", "LegEnvSummary", "summarize_leg_env"]


@dataclass(frozen=True)
class EnvSample:
    """One fused environment sample over a leg (an ``asset_state_history`` row)."""

    time: datetime
    temperature_c: float | None
    humidity_pct: float | None


@dataclass(frozen=True)
class LegEnvSummary:
    """Envelope + (optional) SLA verdict for one closed leg."""

    temp_min_c: float | None
    temp_max_c: float | None
    temp_mean_c: float | None
    humidity_min: float | None
    humidity_max: float | None
    excursion_s: int | None
    in_range_pct: float | None
    sla_breached: bool | None


def _min(vals: list[float]) -> float | None:
    return min(vals) if vals else None


def _max(vals: list[float]) -> float | None:
    return max(vals) if vals else None


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _in_range(sample: EnvSample, sla: SlaConfig) -> bool:
    """Whether a sample is within the SLA envelope.

    A ``None`` reading on a bounded axis is treated as in-range (can't evaluate
    what wasn't measured); an unset bound never fails.
    """
    t = sample.temperature_c
    if t is not None:
        if sla.temp_min_c is not None and t < sla.temp_min_c:
            return False
        if sla.temp_max_c is not None and t > sla.temp_max_c:
            return False
    h = sample.humidity_pct
    return not (h is not None and sla.humidity_max is not None and h > sla.humidity_max)


def _sla_bounded(sla: SlaConfig) -> bool:
    """Whether the SLA actually constrains anything."""
    return any(b is not None for b in (sla.temp_min_c, sla.temp_max_c, sla.humidity_max))


def summarize_leg_env(
    samples: Sequence[EnvSample],
    sla: SlaConfig | None,
) -> LegEnvSummary:
    """Summarize a leg's environment + (optionally) score it against ``sla``.

    ``excursion_s`` is the longest **contiguous** out-of-range run, measured as
    the elapsed time between the run's first and last out-of-range samples (a
    lone out-of-range sample contributes ``0`` unless adjacent ones extend it).
    ``in_range_pct`` is the share of evaluable samples within the envelope.
    ``sla_breached`` is ``True`` when ``excursion_s`` exceeds
    ``excursion_tolerance_s``.
    """
    ordered = sorted(samples, key=lambda s: s.time)
    temps = [s.temperature_c for s in ordered if s.temperature_c is not None]
    hums = [s.humidity_pct for s in ordered if s.humidity_pct is not None]

    envelope = dict(
        temp_min_c=_min(temps),
        temp_max_c=_max(temps),
        temp_mean_c=_mean(temps),
        humidity_min=_min(hums),
        humidity_max=_max(hums),
    )

    if sla is None or not _sla_bounded(sla) or not ordered:
        return LegEnvSummary(**envelope, excursion_s=None, in_range_pct=None, sla_breached=None)

    flags = [_in_range(s, sla) for s in ordered]
    in_range = sum(flags)
    in_range_pct = round(100.0 * in_range / len(flags), 1)

    # Longest contiguous out-of-range run (elapsed time between its boundary
    # samples). A run of one sample has 0 duration.
    longest = 0.0
    run_start: datetime | None = None
    prev: datetime | None = None
    for s, ok in zip(ordered, flags, strict=True):
        if not ok:
            if run_start is None:
                run_start = s.time
            prev = s.time
        else:
            if run_start is not None and prev is not None:
                longest = max(longest, (prev - run_start).total_seconds())
            run_start = None
            prev = None
    if run_start is not None and prev is not None:
        longest = max(longest, (prev - run_start).total_seconds())

    excursion_s = int(longest)
    sla_breached = excursion_s > sla.excursion_tolerance_s
    return LegEnvSummary(
        **envelope,
        excursion_s=excursion_s,
        in_range_pct=in_range_pct,
        sla_breached=sla_breached,
    )
