"""IsolatedZones attribution processor (Sprint 41 Phase D1 / ADR-021 v2).

Pure-function codification of the **existing implicit single-zone
attribution** behaviour that has been in :class:`tagpulse.ingestion.service.IngestionService`
since Sprint 14 (reader-bound) and Sprint 17a (geofence). The algorithm
is unchanged; this module just gives it a name, a documented contract,
and unit-test surface so the OverlappingZones processor (Phase D2) has
a sibling to compare against.

Default ``processor`` value when a signaling rule has
``processor IS NULL`` and ``event_type IN (location, geofencing)`` —
the ingestion pipeline keeps emitting :attr:`tagpulse.events.protocol.Topic.SUBJECT_ZONE_CHANGED`
exactly as it does today and the rules engine consumes it. Nothing else
changes for "isolated" attribution.

Algorithm
---------

A single tag read can match **at most one** zone:

1. **Reader-bound first.** If the read's ``reader_id`` (= ``device_id``)
   appears in any zone's ``fixed_reader_ids``, the deterministically-oldest
   such zone wins. Mirrors
   :meth:`tagpulse.repositories.timescaledb.sites_zones.TimescaleSitesZonesRepository.get_zone_for_reader`
   and the "one zone per reader" rule from
   :doc:`docs/design/assets-and-zones.md` §11 Q4.
2. **Geofence fallback.** If no reader-bound zone matched and the read
   carries a ``(latitude, longitude)``, ray-cast against geofence-zone
   polygons; the deterministically-oldest containing zone wins. Mirrors
   :meth:`tagpulse.ingestion.service.IngestionService._eval_geofence_for_subject`'s
   "first match wins" loop over bbox-prefiltered candidates.
3. Otherwise no attribution — the read carries no zone-resolving signal
   (mobile reader, no GPS, no zone for this reader). Returns ``None``.

This is a strict single-zone result. Sites with intentional zone overlap
(e.g. a "Building A" zone fully containing a "Loading Bay 2" sub-zone)
get *one* of the two attributed and the other silently dropped — the
deterministic-by-``created_at`` tiebreak makes the choice reproducible
but it is still lossy. :mod:`tagpulse.signaling.overlapping_zones`
(Phase D2) is the answer when both attributions matter.

The module is pure: no DB, no IO, no event publishing. Callers materialise
the candidate zone list (typically via the repository's bbox-prefilter
cache) and feed it in. This keeps the algorithm trivially testable and
keeps the existing ingestion call sites' performance profile unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from tagpulse.geo import point_in_polygon

__all__ = [
    "IsolatedZoneAttribution",
    "ZoneCandidate",
    "attribute",
    "attribute_geofence",
    "attribute_reader_bound",
]


@dataclasses.dataclass(frozen=True)
class ZoneCandidate:
    """Materialised zone row passed to the attribution functions.

    Mirrors the subset of :class:`tagpulse.models.database.ZoneModel` the
    attribution algorithm needs. Constructed from a :class:`ZoneResponse`
    or directly from a SQLAlchemy row; the processor doesn't care which.
    ``created_at`` drives the deterministic-oldest tiebreak when multiple
    zones match the same read.
    """

    id: UUID
    kind: str  # 'reader_bound' | 'geofence' | 'virtual'
    created_at: datetime
    fixed_reader_ids: tuple[str, ...] | None = None
    polygon_geojson: dict[str, Any] | None = None
    bbox_min_lat: float | None = None
    bbox_max_lat: float | None = None
    bbox_min_lon: float | None = None
    bbox_max_lon: float | None = None


@dataclasses.dataclass(frozen=True)
class IsolatedZoneAttribution:
    """Result of a successful IsolatedZones attribution.

    ``source`` is ``'reader_bound'`` or ``'geofence'`` so the caller can
    propagate the same ``zone_kind`` discriminator the existing
    ``subject.zone_changed`` event payload carries. The attribution is
    always a single zone — overlap is by definition out of scope for this
    processor (see :mod:`tagpulse.signaling.overlapping_zones` for the
    multi-zone case).
    """

    zone_id: UUID
    zone_kind: str
    source: str


def _sorted_by_created_at(zones: Sequence[ZoneCandidate]) -> list[ZoneCandidate]:
    """Stable sort by ``created_at`` ascending.

    The deterministic-oldest tiebreak is the source of truth for the
    "one zone per reader" rule. Defending against callers that pass an
    unsorted list keeps the algorithm correct regardless of how the
    candidate list was assembled (cache hit, fresh query, hand-built test
    fixture, etc.).
    """

    return sorted(zones, key=lambda z: z.created_at)


def attribute_reader_bound(
    *,
    reader_id: UUID,
    zones: Sequence[ZoneCandidate],
) -> IsolatedZoneAttribution | None:
    """Return the deterministically-oldest reader-bound zone for a reader.

    Mirrors the SQL in
    :meth:`tagpulse.repositories.timescaledb.sites_zones.TimescaleSitesZonesRepository.get_zone_for_reader`
    but operates on an in-memory candidate list so the OverlappingZones
    processor (Phase D2) can reuse it on a much larger fan-in.

    Only zones with ``kind='reader_bound'`` are considered. ``reader_id``
    is compared against the JSONB-stored UUID-as-string list, matching
    the column's wire format.
    """

    target = str(reader_id)
    for zone in _sorted_by_created_at(zones):
        if zone.kind != "reader_bound":
            continue
        readers = zone.fixed_reader_ids or ()
        if target in readers:
            return IsolatedZoneAttribution(
                zone_id=zone.id,
                zone_kind="reader_bound",
                source="reader_bound",
            )
    return None


def attribute_geofence(
    *,
    latitude: float,
    longitude: float,
    zones: Sequence[ZoneCandidate],
) -> IsolatedZoneAttribution | None:
    """Return the deterministically-oldest geofence zone containing the point.

    Mirrors the loop in
    :meth:`tagpulse.ingestion.service.IngestionService._eval_geofence_for_subject`
    but exposed as a pure function. Bbox prefilter runs first as a cheap
    short-circuit; only candidates whose bbox contains the point are
    submitted to the (more expensive) ray-cast.

    Malformed polygons (missing ``coordinates``, wrong shape) are
    silently skipped — same behaviour as the ingestion path, which logs
    a warning at the call site rather than failing the read. We don't
    re-log here because this function may be called millions of times
    per minute by the OverlappingZones processor.
    """

    for zone in _sorted_by_created_at(zones):
        if zone.kind != "geofence":
            continue
        # Cheap bbox short-circuit; mirrors the SQL prefilter in the
        # repository and the Python prefilter in the ingestion path.
        if zone.bbox_min_lat is not None and not (
            zone.bbox_min_lat <= latitude <= (zone.bbox_max_lat or latitude)
            and (zone.bbox_min_lon or longitude) <= longitude <= (zone.bbox_max_lon or longitude)
        ):
            continue
        polygon = zone.polygon_geojson
        if not polygon:
            continue
        try:
            ring_raw = polygon["coordinates"][0]
            ring = [(float(p[0]), float(p[1])) for p in ring_raw]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if point_in_polygon(latitude, longitude, ring):
            return IsolatedZoneAttribution(
                zone_id=zone.id,
                zone_kind="geofence",
                source="geofence",
            )
    return None


def attribute(
    *,
    reader_id: UUID | None,
    latitude: float | None,
    longitude: float | None,
    zones: Sequence[ZoneCandidate],
) -> IsolatedZoneAttribution | None:
    """Convenience: try reader-bound first, then geofence.

    Reproduces the priority the ingestion pipeline applies today —
    reader-bound is a stronger signal (the reader is physically inside
    the zone by setup) than a GPS fix that might be from a mobile
    reader passing through. The OverlappingZones processor uses the
    same priority when an overlap candidate has both a matching
    fixed-reader entry *and* a containing geofence polygon.

    Returns ``None`` when no signal resolves — this is the "mobile
    reader with no GPS" case that emits no zone transition. Callers
    treat ``None`` as "leave the subject's previous zone alone."
    """

    if reader_id is not None:
        result = attribute_reader_bound(reader_id=reader_id, zones=zones)
        if result is not None:
            return result
    if latitude is not None and longitude is not None:
        return attribute_geofence(latitude=latitude, longitude=longitude, zones=zones)
    return None
