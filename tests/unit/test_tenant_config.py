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
