"""Map config resolver (Sprint 17a §11 Q4 Resolved).

Per-tenant tile-provider config consumed by the map UI's
``GET /tenant/map-config`` endpoint. NULL stored config = system default
(OSM public). Pluggable providers: ``osm``, ``mapbox``, ``maptiler``,
``self_hosted``.

Tokens / API keys live in the ``tenants.tile_provider`` JSONB blob — they
are tenant secrets and visible only to ``admin`` and ``editor`` roles via
the read endpoint (``viewer`` gets the resolved URL/attribution but the
provider name is not sensitive).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Per docs/design/geofencing-and-map.md §11 Q4: ship OSM public as the
# zero-config default. Operators are expected to switch to a paid provider
# (Mapbox / MapTiler) before opening the platform to high-traffic users
# given OSM's tile-usage policy.
_DEFAULT_PROVIDER: dict[str, Any] = {
    "kind": "osm",
    "tile_url_template": ("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
    "attribution": (
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    ),
    "max_zoom": 19,
    "subdomains": ["a", "b", "c"],
}


class MapConfigResponse(BaseModel):
    """Resolved tile-provider config returned to the UI."""

    kind: str = Field(description="Provider kind: osm | mapbox | maptiler | self_hosted")
    tile_url_template: str
    attribution: str
    max_zoom: int = 19
    subdomains: list[str] | None = None


class MapConfigError(ValueError):
    """Raised when a stored ``tile_provider`` blob can't be resolved."""


def resolve_map_config(stored: dict[str, Any] | None) -> MapConfigResponse:
    """Return a renderable map config for the tile-provider blob.

    Falls back to the system default (OSM public) when ``stored`` is None.
    Provider-specific URL templating happens here so the UI can stay dumb.
    """
    if stored is None:
        return MapConfigResponse(**_DEFAULT_PROVIDER)
    kind = stored.get("kind")
    if kind == "osm":
        return MapConfigResponse(**_DEFAULT_PROVIDER)
    if kind == "mapbox":
        return _resolve_mapbox(stored)
    if kind == "maptiler":
        return _resolve_maptiler(stored)
    if kind == "self_hosted":
        return _resolve_self_hosted(stored)
    raise MapConfigError(f"unsupported tile_provider.kind: {kind!r}")


def _resolve_mapbox(stored: dict[str, Any]) -> MapConfigResponse:
    style = stored.get("style", "mapbox/streets-v12")
    token = stored.get("access_token")
    if not token:
        raise MapConfigError("mapbox tile_provider requires 'access_token'")
    return MapConfigResponse(
        kind="mapbox",
        tile_url_template=(
            f"https://api.mapbox.com/styles/v1/{style}/tiles/256/"
            f"{{z}}/{{x}}/{{y}}@2x?access_token={token}"
        ),
        attribution=(
            '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a> '
            '&copy; <a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap</a>"
        ),
        max_zoom=int(stored.get("max_zoom", 22)),
    )


def _resolve_maptiler(stored: dict[str, Any]) -> MapConfigResponse:
    style = stored.get("style", "streets-v2")
    key = stored.get("api_key")
    if not key:
        raise MapConfigError("maptiler tile_provider requires 'api_key'")
    return MapConfigResponse(
        kind="maptiler",
        tile_url_template=(
            f"https://api.maptiler.com/maps/{style}/256/{{z}}/{{x}}/{{y}}.png?key={key}"
        ),
        attribution=(
            '&copy; <a href="https://www.maptiler.com/copyright/">MapTiler</a> '
            '&copy; <a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap</a>"
        ),
        max_zoom=int(stored.get("max_zoom", 22)),
    )


def _resolve_self_hosted(stored: dict[str, Any]) -> MapConfigResponse:
    template = stored.get("tile_url_template")
    if not template:
        raise MapConfigError("self_hosted tile_provider requires 'tile_url_template'")
    return MapConfigResponse(
        kind="self_hosted",
        tile_url_template=template,
        attribution=stored.get("attribution", ""),
        max_zoom=int(stored.get("max_zoom", 19)),
        subdomains=stored.get("subdomains"),
    )


__all__ = [
    "MapConfigError",
    "MapConfigResponse",
    "resolve_map_config",
]
