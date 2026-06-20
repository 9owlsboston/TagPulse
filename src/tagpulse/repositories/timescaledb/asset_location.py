"""Read-only queries against ``asset_current_location`` + path queries.

Sprint 15 — closes the [planned] / [deferred → Phase B.3] roadmap items:

* :meth:`get_current_location` — single-asset latest position via the view.
* :meth:`list_current_locations` — bulk variant for the UI Assets list.
* :meth:`get_asset_path` — merged RFID + external timeline for the asset
  detail page, badged by source per
  ``docs/design/mobile-carriers-and-manifests.md`` §10 Q5.
* :meth:`get_assets_in_zone` — power for the Sites & Zones occupancy panel.

All queries assume the caller has already set ``app.current_tenant_id`` (the
session dependency does this); we still pass ``tenant_id`` explicitly so the
SQL is self-documenting and works in non-RLS test fixtures.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.schemas import (
    AssetCurrentLocation,
    AssetInZoneSummary,
    AssetPathPoint,
)


class TimescaleAssetLocationRepository:
    """SQL view-backed reads. No writes — bindings/external/RFID populate it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── current location ───────────────────────────────────────────────────

    _CURRENT_BY_ASSET = text(
        """
        SELECT asset_id, last_seen_at, kind, recorded_at, latitude, longitude,
               accuracy_meters, x, y, site_id, device_id, latest_position_source
        FROM asset_current_location
        WHERE tenant_id = :tenant_id AND asset_id = :asset_id
        """
    )

    async def get_current_location(
        self, tenant_id: uuid.UUID, asset_id: uuid.UUID
    ) -> AssetCurrentLocation | None:
        result = await self._session.execute(
            self._CURRENT_BY_ASSET,
            {"tenant_id": tenant_id, "asset_id": asset_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return AssetCurrentLocation(
            asset_id=row.asset_id,
            last_seen_at=row.last_seen_at,
            kind=row.kind,
            recorded_at=row.recorded_at,
            latitude=row.latitude,
            longitude=row.longitude,
            accuracy_meters=row.accuracy_meters,
            x=float(row.x) if row.x is not None else None,
            y=float(row.y) if row.y is not None else None,
            site_id=row.site_id,
            device_id=row.device_id,
            latest_position_source=row.latest_position_source,
        )

    _CURRENT_LIST = text(
        """
        SELECT asset_id, last_seen_at, kind, recorded_at, latitude, longitude,
               accuracy_meters, x, y, site_id, device_id, latest_position_source
        FROM asset_current_location
        WHERE tenant_id = :tenant_id
        ORDER BY last_seen_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )

    async def list_current_locations(
        self, tenant_id: uuid.UUID, *, limit: int = 200, offset: int = 0
    ) -> Sequence[AssetCurrentLocation]:
        result = await self._session.execute(
            self._CURRENT_LIST,
            {"tenant_id": tenant_id, "limit": limit, "offset": offset},
        )
        return [
            AssetCurrentLocation(
                asset_id=row.asset_id,
                last_seen_at=row.last_seen_at,
                kind=row.kind,
                recorded_at=row.recorded_at,
                latitude=row.latitude,
                longitude=row.longitude,
                accuracy_meters=row.accuracy_meters,
                x=float(row.x) if row.x is not None else None,
                y=float(row.y) if row.y is not None else None,
                site_id=row.site_id,
                device_id=row.device_id,
                latest_position_source=row.latest_position_source,
            )
            for row in result.all()
        ]

    # ── merged path ────────────────────────────────────────────────────────

    _PATH_SQL = text(
        """
        WITH active_bindings AS (
            SELECT binding_value, binding_kind
            FROM asset_tag_bindings
            WHERE tenant_id = :tenant_id AND asset_id = :asset_id
              AND unbound_at IS NULL
        ),
        rfid AS (
            SELECT
                tr."timestamp"           AS recorded_at,
                tr.latitude              AS latitude,
                tr.longitude             AS longitude,
                tr.location_accuracy_m   AS accuracy_meters,
                'rfid'::text             AS source,
                tr.device_id             AS device_id,
                tr.id                    AS tag_read_id,
                NULL::uuid               AS external_id
            FROM tag_reads tr
            JOIN active_bindings b
              ON (b.binding_kind = 'epc'    AND tr.epc    = b.binding_value)
              OR (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value)
              OR (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
            WHERE tr.tenant_id = :tenant_id
              AND tr.latitude  IS NOT NULL
              AND tr.longitude IS NOT NULL
              AND tr."timestamp" >= :since
              AND tr."timestamp" <  :until
        ),
        ext AS (
            SELECT
                el.recorded_at      AS recorded_at,
                el.latitude         AS latitude,
                el.longitude        AS longitude,
                el.accuracy_meters  AS accuracy_meters,
                COALESCE(el.source, 'external')::text AS source,
                NULL::uuid          AS device_id,
                NULL::uuid          AS tag_read_id,
                el.id               AS external_id
            FROM external_locations el
            WHERE el.tenant_id = :tenant_id
              AND el.asset_id  = :asset_id
              AND el.recorded_at >= :since
              AND el.recorded_at <  :until
        )
        SELECT * FROM rfid
        UNION ALL
        SELECT * FROM ext
        ORDER BY recorded_at ASC
        LIMIT :limit
        """
    )

    async def get_asset_path(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int = 1000,
    ) -> Sequence[AssetPathPoint]:
        result = await self._session.execute(
            self._PATH_SQL,
            {
                "tenant_id": tenant_id,
                "asset_id": asset_id,
                "since": since,
                "until": until,
                "limit": limit,
            },
        )
        return [
            AssetPathPoint(
                recorded_at=row.recorded_at,
                latitude=row.latitude,
                longitude=row.longitude,
                accuracy_meters=row.accuracy_meters,
                source=row.source,
                device_id=row.device_id,
                tag_read_id=row.tag_read_id,
                external_id=row.external_id,
            )
            for row in result.all()
        ]

    # ── occupancy ──────────────────────────────────────────────────────────

    # Reader-bound zones: an asset is "in" the zone if the latest tag-read
    # against any of its active bindings was produced by one of the zone's
    # ``fixed_reader_ids`` (which are device UUIDs serialised as JSONB strings).
    _ASSETS_IN_READER_BOUND_ZONE = text(
        """
        WITH last_read_per_binding AS (
            SELECT DISTINCT ON (b.asset_id)
                b.asset_id,
                b.binding_value,
                b.binding_kind,
                tr.device_id,
                tr."timestamp" AS last_seen_at
            FROM asset_tag_bindings b
            JOIN tag_reads tr
              ON tr.tenant_id = b.tenant_id
             AND (
                    (b.binding_kind = 'epc'    AND tr.epc    = b.binding_value) OR
                    (b.binding_kind = 'tid'    AND tr.tid    = b.binding_value) OR
                    (b.binding_kind = 'device' AND tr.tag_id = b.binding_value)
                 )
            WHERE b.tenant_id = :tenant_id
              AND b.unbound_at IS NULL
            ORDER BY b.asset_id, tr."timestamp" DESC
        )
        SELECT
            a.id      AS asset_id,
            a.name    AS name,
            lr.last_seen_at,
            lr.binding_value,
            lr.binding_kind
        FROM last_read_per_binding lr
        JOIN assets a ON a.id = lr.asset_id AND a.tenant_id = :tenant_id
        JOIN zones z
          ON z.tenant_id = :tenant_id
         AND z.id = :zone_id
         AND z.kind = 'reader_bound'
         AND z.fixed_reader_ids ? lr.device_id::text
        WHERE a.status != 'retired'
        ORDER BY lr.last_seen_at DESC
        LIMIT :limit OFFSET :offset
        """
    )

    # Geofence zones: an asset is "in" the zone if its current position
    # (from the ``asset_current_location`` view, which already picks the
    # newest of RFID + external per asset) falls inside the zone's polygon.
    # Querying the view rather than ``tag_reads`` directly guarantees we
    # see the *latest* fix — bbox-prefiltering inside a per-binding
    # DISTINCT ON would silently keep stale inside-bbox reads after an asset
    # exited (since newer outside-bbox reads would be filtered out before
    # the "pick latest" step).
    _ASSETS_LATEST_GPS_READ = text(
        """
        SELECT
            acl.asset_id,
            acl.recorded_at AS last_seen_at,
            acl.latitude,
            acl.longitude,
            a.name        AS name,
            b.binding_value,
            b.binding_kind
        FROM asset_current_location acl
        JOIN assets a
          ON a.id = acl.asset_id
         AND a.tenant_id = acl.tenant_id
        LEFT JOIN LATERAL (
            SELECT binding_value, binding_kind
            FROM asset_tag_bindings
            WHERE tenant_id = acl.tenant_id
              AND asset_id  = acl.asset_id
              AND unbound_at IS NULL
            ORDER BY bound_at DESC
            LIMIT 1
        ) b ON TRUE
        WHERE acl.tenant_id = :tenant_id
          AND a.status != 'retired'
          AND acl.latitude  BETWEEN :min_lat AND :max_lat
          AND acl.longitude BETWEEN :min_lon AND :max_lon
        """
    )

    async def get_assets_in_zone(
        self,
        tenant_id: uuid.UUID,
        zone_id: uuid.UUID,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[AssetInZoneSummary]:
        """Currently-in-zone assets, judged by latest tag read per binding.

        For ``reader_bound`` zones, "in zone" = the reader (``device_id``) of
        the latest tag read for any active binding is one of the zone's
        ``fixed_reader_ids``. For ``geofence`` zones, "in zone" = the latest
        GPS-bearing tag read for any active binding falls inside the zone's
        polygon (bbox-prefiltered in SQL, point-in-polygon in Python). Both
        match the rule the ingestion enrichment uses to emit
        ``subject.zone_changed``, so this panel and the Alerts page agree.
        """
        zone_row = (
            await self._session.execute(
                text(
                    "SELECT kind, polygon_geojson, "
                    "bbox_min_lat, bbox_max_lat, "
                    "bbox_min_lon, bbox_max_lon "
                    "FROM zones "
                    "WHERE tenant_id = :tenant_id AND id = :zone_id"
                ),
                {"tenant_id": tenant_id, "zone_id": zone_id},
            )
        ).one_or_none()
        if zone_row is None:
            return []

        if zone_row.kind == "reader_bound":
            result = await self._session.execute(
                self._ASSETS_IN_READER_BOUND_ZONE,
                {
                    "tenant_id": tenant_id,
                    "zone_id": zone_id,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return [
                AssetInZoneSummary(
                    asset_id=row.asset_id,
                    name=row.name,
                    last_seen_at=row.last_seen_at,
                    binding_value=row.binding_value,
                    binding_kind=row.binding_kind,
                )
                for row in result.all()
            ]

        if zone_row.kind == "geofence":
            polygon = zone_row.polygon_geojson
            if (
                polygon is None
                or zone_row.bbox_min_lat is None
                or zone_row.bbox_max_lat is None
                or zone_row.bbox_min_lon is None
                or zone_row.bbox_max_lon is None
            ):
                return []
            try:
                ring_raw = polygon["coordinates"][0]
                ring = [(float(p[0]), float(p[1])) for p in ring_raw]
            except (KeyError, IndexError, TypeError, ValueError):
                return []
            from tagpulse.geo import point_in_polygon

            result = await self._session.execute(
                self._ASSETS_LATEST_GPS_READ,
                {
                    "tenant_id": tenant_id,
                    "min_lat": zone_row.bbox_min_lat,
                    "max_lat": zone_row.bbox_max_lat,
                    "min_lon": zone_row.bbox_min_lon,
                    "max_lon": zone_row.bbox_max_lon,
                },
            )
            inside = [
                AssetInZoneSummary(
                    asset_id=row.asset_id,
                    name=row.name,
                    last_seen_at=row.last_seen_at,
                    binding_value=row.binding_value,
                    binding_kind=row.binding_kind,
                )
                for row in result.all()
                if point_in_polygon(row.latitude, row.longitude, ring)
            ]
            inside.sort(key=lambda r: r.last_seen_at, reverse=True)
            return inside[offset : offset + limit]

        return []
