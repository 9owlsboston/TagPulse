"""Indoor floor-position estimator — ``rssi_weighted_centroid`` (Sprint 66).

Phase 2 part 1 of [floor-position-estimation.md](../../../docs/design/floor-position-estimation.md)
and the [ADR-024](../../../docs/adr/024-position-estimation.md) amendment: the
**pure, I/O-free** algorithmic core that turns a set of per-antenna observations
of one asset into a floor ``(x, y)`` fix. The worker/pipeline that feeds it from
``tag_reads`` and writes ``asset_positions(source='computed')`` is a separate,
later slice; this module is deliberately decoupled so it is trivially testable.

Reference algorithm (ADR-024 v2): **relative, recency-decayed, hull-bounded
centroid**.

- **Relative RSSI** — each antenna's weight uses ``rssi − min_rssi + 1`` over the
  contributing antennas, so it needs no absolute calibration (no path-loss model).
- **Recency decay** — each weight is multiplied by ``0.5 ** (Δt / τ)`` where ``τ``
  is the per-tenant ``half_life_s``. ``τ → 0`` collapses to *last-one-wins* (only
  the freshest antenna survives → a choke-point answer); ``τ`` large approaches a
  plain centroid.
- **Hull-bounded for free** — a weighted average with non-negative weights is a
  convex combination of the antenna coordinates, so the result is **always inside
  their convex hull**. No explicit clamping is needed; 1 antenna ⇒ its point,
  2 ⇒ on the segment, 3+ ⇒ inside the polygon.

The exact weight formula is per-tenant :class:`PositionStrategy` config, never
hardcoded (ADR-024). ``cnt`` (reads/cycle) is carried on the observation for a
future count-weight extension but does not influence the reference weight (so the
reference matches the documented worked example).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PositionStrategy(BaseModel):
    """Per-tenant estimator config (the ``tenants.position_strategy`` JSONB).

    Two primary knobs: ``half_life_s`` (τ — the recency dial; ``0`` = last-wins)
    and ``recompute_interval_s`` (D — the server-side recompute cadence, consumed
    by the future worker, not by this pure estimator). The guards bound which
    observations contribute.
    """

    strategy: Literal["rssi_weighted_centroid"] = "rssi_weighted_centroid"
    half_life_s: float = Field(default=5.0, ge=0.0)
    recompute_interval_s: float = Field(default=3.0, gt=0.0)
    lookback_s: float = Field(default=15.0, gt=0.0)
    min_antennas: int = Field(default=1, ge=1)
    rssi_floor_dbm: float = Field(default=-75.0)


@dataclass(frozen=True)
class AntennaObservation:
    """One asset-on-antenna observation fed to the estimator.

    ``x``/``y`` are the antenna's floor-frame coordinates (from the ``antennas``
    table). ``ts`` is the server ingest time of the observation (Sprint 65 D2 —
    no cross-reader clock comparison). The estimator is per-asset: the caller has
    already fused EPC → asset and gathered that asset's observations.
    """

    antenna_id: UUID
    x: float
    y: float
    rssi: float
    cnt: int
    ts: datetime


@dataclass(frozen=True)
class PositionFix:
    """An estimated floor ``(x, y)`` with an honest ``confidence`` in ``[0, 1]``."""

    x: float
    y: float
    confidence: float
    antenna_count: int


def _confidence(distinct: int, decays: Sequence[float]) -> float:
    """Honest confidence: geometry (antenna count) × freshness (Σ decay)."""
    geom = {1: 0.30, 2: 0.45}.get(distinct, min(0.80, 0.60 + 0.05 * (distinct - 3)))
    freshness = sum(decays) / distinct if distinct else 0.0
    return round(max(0.0, min(1.0, geom * freshness)), 2)


def rssi_weighted_centroid(
    observations: Sequence[AntennaObservation],
    *,
    now: datetime,
    config: PositionStrategy,
) -> PositionFix | None:
    """Estimate one asset's floor ``(x, y)`` from its per-antenna observations.

    Returns ``None`` when, after filtering by ``rssi_floor_dbm`` and
    ``lookback_s``, fewer than ``min_antennas`` distinct antennas remain.

    Steps (ADR-024 v2):
    1. Drop observations weaker than ``rssi_floor_dbm`` or older than
       ``lookback_s``.
    2. Group by antenna; keep the **strongest** observation per antenna (best
       oriented tag face).
    3. Weight each kept antenna by ``(rssi − min_rssi + 1) · 0.5 ** (Δt / τ)``
       and take the centroid (``τ → 0`` ⇒ only the freshest antenna).
    """
    # 1. Filter.
    kept_by_antenna: dict[UUID, AntennaObservation] = {}
    for obs in observations:
        if obs.rssi < config.rssi_floor_dbm:
            continue
        age = (now - obs.ts).total_seconds()
        if age > config.lookback_s:
            continue
        # 2. Strongest per antenna.
        prior = kept_by_antenna.get(obs.antenna_id)
        if prior is None or obs.rssi > prior.rssi:
            kept_by_antenna[obs.antenna_id] = obs

    kept = list(kept_by_antenna.values())
    if len(kept) < config.min_antennas:
        return None

    # τ → 0: last-one-wins — keep only the freshest antenna (choke-point).
    if config.half_life_s <= 0.0:
        freshest = max(kept, key=lambda o: o.ts)
        kept = [freshest]

    rssi_min = min(o.rssi for o in kept)

    weights: list[float] = []
    decays: list[float] = []
    for obs in kept:
        if config.half_life_s <= 0.0:
            decay = 1.0
        else:
            age = max(0.0, (now - obs.ts).total_seconds())
            decay = 0.5 ** (age / config.half_life_s)
        decays.append(decay)
        weights.append((obs.rssi - rssi_min + 1.0) * decay)

    total = sum(weights)
    if total <= 0.0:
        return None

    x = sum(w * o.x for w, o in zip(weights, kept, strict=True)) / total
    y = sum(w * o.y for w, o in zip(weights, kept, strict=True)) / total

    return PositionFix(
        x=x,
        y=y,
        confidence=_confidence(len(kept), decays),
        antenna_count=len(kept),
    )
