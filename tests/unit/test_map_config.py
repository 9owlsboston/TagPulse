"""Unit tests for ``tagpulse.services.map_config``."""

from __future__ import annotations

import pytest

from tagpulse.services.map_config import (
    MapConfigError,
    resolve_map_config,
)


def test_default_is_osm_when_none_stored() -> None:
    cfg = resolve_map_config(None)
    assert cfg.kind == "osm"
    assert "openstreetmap" in cfg.tile_url_template.lower()
    assert cfg.subdomains == ["a", "b", "c"]


def test_explicit_osm_returns_default_template() -> None:
    cfg = resolve_map_config({"kind": "osm"})
    assert cfg.kind == "osm"
    assert "{z}/{x}/{y}" in cfg.tile_url_template


def test_mapbox_requires_token() -> None:
    with pytest.raises(MapConfigError, match="access_token"):
        resolve_map_config({"kind": "mapbox"})


def test_mapbox_with_token_renders_template() -> None:
    cfg = resolve_map_config({"kind": "mapbox", "access_token": "pk.test"})
    assert cfg.kind == "mapbox"
    assert "access_token=pk.test" in cfg.tile_url_template
    assert "mapbox" in cfg.attribution.lower()


def test_maptiler_requires_api_key() -> None:
    with pytest.raises(MapConfigError, match="api_key"):
        resolve_map_config({"kind": "maptiler"})


def test_maptiler_renders_template() -> None:
    cfg = resolve_map_config({"kind": "maptiler", "api_key": "sk.test", "style": "topo-v2"})
    assert "topo-v2" in cfg.tile_url_template
    assert "key=sk.test" in cfg.tile_url_template


def test_self_hosted_requires_template() -> None:
    with pytest.raises(MapConfigError, match="tile_url_template"):
        resolve_map_config({"kind": "self_hosted"})


def test_self_hosted_passes_through() -> None:
    cfg = resolve_map_config(
        {
            "kind": "self_hosted",
            "tile_url_template": "https://tiles.example.com/{z}/{x}/{y}.png",
            "attribution": "&copy; Example",
            "max_zoom": 18,
        }
    )
    assert cfg.kind == "self_hosted"
    assert cfg.max_zoom == 18
    assert cfg.attribution == "&copy; Example"


def test_unsupported_kind_raises() -> None:
    with pytest.raises(MapConfigError, match="unsupported"):
        resolve_map_config({"kind": "google"})
