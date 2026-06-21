"""Asset state consolidation — the pure fusion core (Sprint 71, [ADR-034]).

Phase 1 of [asset state consolidation](../../../docs/design/sprint-71-asset-state-consolidation.md):
the **pure, I/O-free** algorithm that fuses one asset's bound-tag reads (tags
``a``, ``b``, ``c`` …) over a look-back window into a single asset-level answer:

- **Location** — a ``read_count × recency``-weighted **vote** over the
  ``(frame, zone)`` each read resolves to. Frames are mostly temporally
  exclusive (an asset is in reader-world *or* geo-world); the recency decay
  arbitrates the brief handoff overlap (dock-reader ⇄ truck-geo) automatically.
- **Environment** — a ``read_count × recency``-weighted **mean** of temperature
  and humidity. Frame-agnostic: the cold chain is continuous whether the lot is
  on a dock, on the highway, or in a store chiller.

The **same weight** drives both, so location and environment stay mutually
consistent (they are the same reads). The worker/source that materialises
:class:`ResolvedRead` rows from ``tag_reads`` (binding resolution + zone/geo
resolution) and persists the snapshot to ``asset_state_history`` is a separate
slice (:mod:`tagpulse.workers.consolidation_worker`); this module is deliberately
decoupled so the algorithm is trivially testable from a hand-built fixture.

Mirrors the recency-decay shape of the floor-position estimator
(:mod:`tagpulse.services.positioning`); the knobs live in the per-tenant
``tenants.fusion_strategy`` JSONB (:class:`FusionStrategy`), never hardcoded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = [
    "AssetStateSnapshot",
    "FusionStrategy",
    "ResolvedRead",
    "SlaConfig",
    "consolidate",
]


# Frames an asset read can resolve to. ``reader``/``floor`` carry a zone;
# ``geo`` is "in transit" (last GPS fix, no facility zone in Phase 1 — route
# legs are Phase 2); ``none`` = resolvable to neither a zone nor a GPS fix.
Frame = Literal["reader", "floor", "geo", "none"]
_LOCATING_FRAMES: frozenset[str] = frozenset({"reader", "floor", "geo"})


class SlaConfig(BaseModel):
    """Per-tenant cold-chain envelope used to score transit legs (Sprint 72).

    A reading is in-range when ``temp_min_c ≤ temperature ≤ temp_max_c`` and
    ``humidity ≤ humidity_max`` (each bound optional → unbounded on that side).
    ``excursion_tolerance_s`` is the longest contiguous out-of-range run allowed
    before a leg is flagged ``sla_breached``.
    """

    temp_min_c: float | None = Field(default=None)
    temp_max_c: float | None = Field(default=None)
    humidity_max: float | None = Field(default=None)
    excursion_tolerance_s: int = Field(default=0, ge=0)


class FusionStrategy(BaseModel):
    """Per-tenant consolidation config (the ``tenants.fusion_strategy`` JSONB).

    The recency dial ``half_life_s`` (τ) is **shared** across the location vote
    and the environment mean (``τ → 0`` collapses to *last-writer-wins* — only
    the freshest read survives). ``recompute_interval_s`` (D) is the worker tick
    cadence and ``lookback_s`` the window each tick consolidates over (both
    consumed by the worker, not this pure core). ``rssi_floor_dbm`` drops weak
    reads from the *location* vote (``None`` disables the floor); environment
    readings are never RSSI-gated. ``sla`` (Sprint 72) is the optional per-tenant
    cold-chain envelope used to score transit legs; ``None`` = no SLA scoring.
    """

    half_life_s: float = Field(default=5.0, ge=0.0)
    recompute_interval_s: float = Field(default=10.0, gt=0.0)
    lookback_s: float = Field(default=60.0, gt=0.0)
    rssi_floor_dbm: float | None = Field(default=None)
    min_reads: int = Field(default=1, ge=1)
    sla: SlaConfig | None = Field(default=None)


@dataclass(frozen=True)
class ResolvedRead:
    """One bound-tag read, already resolved to a frame/zone + carrying sensors.

    The caller (worker/source) has fused tag → asset and resolved each read to
    its ``(frame, zone)``. ``ts`` is the server ingest time (no cross-reader
    clock comparison). ``tag_key`` is the binding value (EPC/TID/device id) used
    only to count *distinct contributing tags*. Any location/environment field
    may be ``None`` when the read does not carry it.
    """

    asset_id: UUID
    tag_key: str
    ts: datetime
    read_count: int = 1
    rssi: float | None = None
    # Location — frame + the zone/position it resolved to.
    frame: Frame = "none"
    zone_id: UUID | None = None
    site_id: UUID | None = None
    lat: float | None = None
    lon: float | None = None
    x: float | None = None
    y: float | None = None
    # Environment (frame-agnostic).
    temperature_c: float | None = None
    humidity_pct: float | None = None


@dataclass(frozen=True)
class AssetStateSnapshot:
    """The fused asset-level answer for one consolidation tick."""

    asset_id: UUID
    time: datetime
    frame: Frame
    zone_id: UUID | None
    site_id: UUID | None
    lat: float | None
    lon: float | None
    x: float | None
    y: float | None
    temperature_c: float | None
    humidity_pct: float | None
    sample_count: int
    tag_count: int
    confidence: float


def _decay(age_s: float, half_life_s: float) -> float:
    """Recency weight ``0.5 ** (Δt / τ)`` (``τ ≤ 0`` handled by the caller)."""
    return float(0.5 ** (max(0.0, age_s) / half_life_s))


def _weighted_mean(
    pairs: Sequence[tuple[float, float]],
) -> float | None:
    """Weighted mean of ``(value, weight)`` pairs; ``None`` if no weight."""
    total = sum(w for _, w in pairs)
    if total <= 0.0:
        return None
    return sum(v * w for v, w in pairs) / total


def consolidate(
    reads: Sequence[ResolvedRead],
    *,
    asset_id: UUID,
    now: datetime,
    config: FusionStrategy,
) -> AssetStateSnapshot | None:
    """Fuse one asset's resolved reads into a single :class:`AssetStateSnapshot`.

    Returns ``None`` when, after dropping reads older than ``lookback_s``, fewer
    than ``min_reads`` remain.

    Steps:
    1. **Window cut** — drop reads older than ``lookback_s``.
    2. **last-wins** — when ``half_life_s ≤ 0`` keep only the single freshest read.
    3. **Weight** — ``w = read_count · 0.5 ** (Δt / τ)`` per kept read.
    4. **Location vote** — bucket the *locating* reads (frame ∈ reader/floor/geo,
       passing the RSSI floor) by ``(frame, zone_id)``; the max-``Σw`` bucket wins
       and sets ``frame``/``zone_id``/``site_id`` + a weighted-centroid position.
    5. **Environment mean** — ``Σ(w·value)/Σw`` for temperature and humidity over
       *all* kept reads carrying the value (frame-agnostic).
    6. **Confidence** — winning bucket's weight share × mean freshness.
    """
    # 1. Window cut.
    kept = [r for r in reads if (now - r.ts).total_seconds() <= config.lookback_s]
    if len(kept) < config.min_reads:
        return None

    # 2. τ → 0: last-writer-wins (single freshest read drives everything).
    if config.half_life_s <= 0.0:
        kept = [max(kept, key=lambda r: r.ts)]

    # 3. Weight per kept read (shared by location + environment).
    def weight_of(r: ResolvedRead) -> float:
        rc = r.read_count if r.read_count >= 1 else 1
        if config.half_life_s <= 0.0:
            return float(rc)
        return rc * _decay((now - r.ts).total_seconds(), config.half_life_s)

    weights = {id(r): weight_of(r) for r in kept}

    # 4. Location vote — bucket locating reads by (frame, zone_id).
    @dataclass
    class _Bucket:
        weight: float = 0.0
        newest: datetime | None = None
        site_id: UUID | None = None
        reads: list[ResolvedRead] | None = None

    buckets: dict[tuple[str, UUID | None], _Bucket] = {}
    located_weight = 0.0
    for r in kept:
        if r.frame not in _LOCATING_FRAMES:
            continue
        if (
            config.rssi_floor_dbm is not None
            and r.rssi is not None
            and r.rssi < config.rssi_floor_dbm
        ):
            continue
        w = weights[id(r)]
        key = (r.frame, r.zone_id)
        b = buckets.get(key)
        if b is None:
            b = _Bucket(reads=[])
            buckets[key] = b
        assert b.reads is not None
        b.weight += w
        b.reads.append(r)
        b.site_id = r.site_id
        if b.newest is None or r.ts > b.newest:
            b.newest = r.ts
        located_weight += w

    frame: Frame = "none"
    zone_id: UUID | None = None
    site_id: UUID | None = None
    lat = lon = x = y = None
    location_share = 0.0
    if buckets:
        # Winner: max weight, tie-break on the most recent read.
        win_key, win = max(
            buckets.items(),
            key=lambda kv: (kv[1].weight, kv[1].newest or now),
        )
        frame = win_key[0]  # type: ignore[assignment]
        zone_id = win_key[1]
        site_id = win.site_id
        assert win.reads is not None
        lat = _weighted_mean([(r.lat, weights[id(r)]) for r in win.reads if r.lat is not None])
        lon = _weighted_mean([(r.lon, weights[id(r)]) for r in win.reads if r.lon is not None])
        x = _weighted_mean([(r.x, weights[id(r)]) for r in win.reads if r.x is not None])
        y = _weighted_mean([(r.y, weights[id(r)]) for r in win.reads if r.y is not None])
        location_share = win.weight / located_weight if located_weight > 0 else 0.0

    # 5. Environment mean (frame-agnostic, over all kept reads).
    temperature_c = _weighted_mean(
        [(r.temperature_c, weights[id(r)]) for r in kept if r.temperature_c is not None]
    )
    humidity_pct = _weighted_mean(
        [(r.humidity_pct, weights[id(r)]) for r in kept if r.humidity_pct is not None]
    )

    # 6. Confidence — location share × mean freshness of kept reads.
    if config.half_life_s <= 0.0:
        freshness = 1.0
    else:
        decays = [_decay((now - r.ts).total_seconds(), config.half_life_s) for r in kept]
        freshness = sum(decays) / len(decays) if decays else 0.0
    base = location_share if buckets else 0.0
    confidence = round(max(0.0, min(1.0, base * freshness)), 2)

    return AssetStateSnapshot(
        asset_id=asset_id,
        time=now,
        frame=frame,
        zone_id=zone_id,
        site_id=site_id,
        lat=lat,
        lon=lon,
        x=x,
        y=y,
        temperature_c=temperature_c,
        humidity_pct=humidity_pct,
        sample_count=len(kept),
        tag_count=len({r.tag_key for r in kept}),
        confidence=confidence,
    )
