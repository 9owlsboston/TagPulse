"""Unit tests for the tenant config Pydantic surface (Sprint 15b Phase F)."""

from __future__ import annotations

import pytest

from tagpulse.api.routes.tenant_config import TenantConfig, TenantConfigUpdate


def test_update_accepts_single_mode() -> None:
    payload = TenantConfigUpdate(tracking_modes=["asset"])
    assert payload.tracking_modes == ["asset"]


def test_update_accepts_both_modes() -> None:
    payload = TenantConfigUpdate(tracking_modes=["asset", "inventory"])
    assert sorted(payload.tracking_modes) == ["asset", "inventory"]


def test_update_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        TenantConfigUpdate(tracking_modes=[])


def test_update_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        TenantConfigUpdate(tracking_modes=["bogus"])  # type: ignore[list-item]


def test_response_serialises_tracking_modes() -> None:
    cfg = TenantConfig(
        id="00000000-0000-0000-0000-000000000001",
        name="Acme",
        slug="acme",
        plan="standard",
        tracking_modes=["asset", "inventory"],
    )
    dumped = cfg.model_dump()
    assert dumped["tracking_modes"] == ["asset", "inventory"]
    assert dumped["slug"] == "acme"


# Sprint 54 Phase 54.3: per-tenant low_stock_threshold powering
# DashboardSummary.low_stock_count. Bounds enforced on the Field
# so operator typos surface as 422 before we touch the row.


def test_update_accepts_low_stock_threshold() -> None:
    payload = TenantConfigUpdate(low_stock_threshold=5)
    assert payload.low_stock_threshold == 5


@pytest.mark.parametrize("bad", [0, -1, 10_001])
def test_update_rejects_out_of_bounds_low_stock_threshold(bad: int) -> None:
    with pytest.raises(ValueError):
        TenantConfigUpdate(low_stock_threshold=bad)


def test_response_includes_low_stock_threshold_default() -> None:
    cfg = TenantConfig(
        id="00000000-0000-0000-0000-000000000001",
        name="Acme",
        slug="acme",
        plan="standard",
        tracking_modes=["asset"],
    )
    assert cfg.low_stock_threshold == 3


# Sprint 54 follow-up: per-tenant dashboard_tags_count_mode powering
# DashboardSummary.tags_total. Literal validates the enum on PATCH.


def test_update_accepts_known_tag_count_modes() -> None:
    for mode in ("all", "live", "non_terminal"):
        payload = TenantConfigUpdate(dashboard_tags_count_mode=mode)  # type: ignore[arg-type]
        assert payload.dashboard_tags_count_mode == mode


def test_update_rejects_unknown_tag_count_mode() -> None:
    with pytest.raises(ValueError):
        TenantConfigUpdate(dashboard_tags_count_mode="bogus")  # type: ignore[arg-type]


def test_response_includes_dashboard_tags_count_mode_default() -> None:
    cfg = TenantConfig(
        id="00000000-0000-0000-0000-000000000001",
        name="Acme",
        slug="acme",
        plan="standard",
        tracking_modes=["asset"],
    )
    assert cfg.dashboard_tags_count_mode == "live"


# Sprint 73: per-tenant fusion_strategy (asset-state consolidation config).
# PATCH uses presence (model_fields_set) so the UI can set OR clear it.


def test_update_accepts_fusion_strategy() -> None:
    payload = TenantConfigUpdate(fusion_strategy={"half_life_s": 8.0, "lookback_s": 90.0})  # type: ignore[arg-type]
    assert payload.fusion_strategy is not None
    assert payload.fusion_strategy.half_life_s == 8.0
    assert payload.fusion_strategy.lookback_s == 90.0
    assert "fusion_strategy" in payload.model_fields_set


def test_update_accepts_fusion_strategy_with_sla() -> None:
    payload = TenantConfigUpdate(
        fusion_strategy={"sla": {"temp_min_c": 2, "temp_max_c": 8}}  # type: ignore[arg-type]
    )
    assert payload.fusion_strategy is not None
    assert payload.fusion_strategy.sla is not None
    assert payload.fusion_strategy.sla.temp_max_c == 8


def test_update_fusion_strategy_explicit_null_is_clear() -> None:
    payload = TenantConfigUpdate(fusion_strategy=None)
    assert payload.fusion_strategy is None
    assert "fusion_strategy" in payload.model_fields_set  # explicit null = opt out


def test_update_fusion_strategy_omitted_not_in_fields_set() -> None:
    payload = TenantConfigUpdate(tracking_modes=["asset"])
    assert "fusion_strategy" not in payload.model_fields_set


def test_update_rejects_bad_fusion_strategy() -> None:
    with pytest.raises(ValueError):
        TenantConfigUpdate(fusion_strategy={"half_life_s": -1})  # type: ignore[arg-type]


def test_response_includes_fusion_strategy() -> None:
    cfg = TenantConfig(
        id="00000000-0000-0000-0000-000000000001",
        name="Acme",
        slug="acme",
        plan="standard",
        tracking_modes=["asset"],
        fusion_strategy={"half_life_s": 5.0},  # type: ignore[arg-type]
    )
    dumped = cfg.model_dump()
    assert dumped["fusion_strategy"]["half_life_s"] == 5.0
