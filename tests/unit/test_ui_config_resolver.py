"""Unit tests for the Configurable UI resolver + schema (Sprint 60, ADR-032).

Increment 1 (ADR-032 §7 step 1): system defaults only. These tests pin the
schema (curated, presentation-only, camelCase wire keys) and the per-leaf
deep-merge engine the later increments feed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.services.ui_config import (
    SYSTEM_DEFAULT_UI_CONFIG,
    ColumnGroup,
    ThemeConfig,
    UiConfig,
    deep_merge,
    resolve_ui_config,
)

# -- system default --------------------------------------------------------


def test_system_default_is_empty_today_ui() -> None:
    """Configuring nothing reproduces today's UI: no skins, default theme,
    nothing hidden (ADR-032 §3, §7 step 1)."""
    cfg = SYSTEM_DEFAULT_UI_CONFIG
    assert cfg.labels == {}
    assert cfg.nav.hidden == [] and cfg.nav.order == []
    assert cfg.cards == {} and cfg.columns == {} and cfg.tables == {}
    assert cfg.theme.variant == "default"
    assert cfg.theme.card_style == "default"


def test_system_default_serialises_camelcase() -> None:
    """The wire document uses the ADR-032 §4 camelCase keys."""
    dumped = SYSTEM_DEFAULT_UI_CONFIG.model_dump(by_alias=True)
    assert dumped["theme"] == {"variant": "default", "cardStyle": "default"}
    assert set(dumped) == {"labels", "theme", "nav", "cards", "columns", "tables"}


# -- schema guardrails -----------------------------------------------------


def test_unknown_top_level_key_rejected() -> None:
    """Curated surface, not a free JSON dump (ADR-032 §6.1)."""
    with pytest.raises(ValidationError):
        UiConfig.model_validate({"behaviour": {"ingest": "off"}})


def test_unknown_leaf_key_rejected() -> None:
    with pytest.raises(ValidationError):
        ThemeConfig.model_validate({"variant": "operator", "bogus": 1})


def test_camelcase_alias_round_trips() -> None:
    theme = ThemeConfig.model_validate({"variant": "operator", "cardStyle": "sparkline"})
    assert theme.card_style == "sparkline"
    assert theme.model_dump(by_alias=True)["cardStyle"] == "sparkline"


def test_advanced_columns_leaf() -> None:
    """The TID/metadata ask: default-hidden 'advanced' columns (ADR-032 §4)."""
    col = ColumnGroup.model_validate({"hidden": ["metadata"], "advanced": ["tid"]})
    assert col.advanced == ["tid"]
    assert col.hidden == ["metadata"]
    assert col.order == []


def test_bad_sort_dir_rejected() -> None:
    with pytest.raises(ValidationError):
        UiConfig.model_validate(
            {"tables": {"assets": {"defaultSort": {"key": "name", "dir": "up"}}}}
        )


# -- deep_merge ------------------------------------------------------------


def test_deep_merge_override_wins_per_key() -> None:
    base = {"theme": {"variant": "default", "cardStyle": "default"}}
    override = {"theme": {"cardStyle": "sparkline"}}
    merged = deep_merge(base, override)
    # variant falls through from base; cardStyle is overridden (ADR-032 §2).
    assert merged == {"theme": {"variant": "default", "cardStyle": "sparkline"}}


def test_deep_merge_lists_replace_wholesale() -> None:
    base = {"nav": {"hidden": ["a", "b"]}}
    override = {"nav": {"hidden": ["c"]}}
    assert deep_merge(base, override) == {"nav": {"hidden": ["c"]}}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"theme": {"variant": "default"}}
    override = {"theme": {"variant": "operator"}}
    deep_merge(base, override)
    assert base == {"theme": {"variant": "default"}}
    assert override == {"theme": {"variant": "operator"}}


def test_deep_merge_adds_new_keys() -> None:
    base = {"labels": {}}
    override = {"labels": {"device": "Reader"}, "columns": {"assets": {"hidden": ["tid"]}}}
    merged = deep_merge(base, override)
    assert merged["labels"] == {"device": "Reader"}
    assert merged["columns"] == {"assets": {"hidden": ["tid"]}}


# -- resolve_ui_config -----------------------------------------------------


def test_resolve_no_overrides_is_system_default() -> None:
    assert resolve_ui_config() == SYSTEM_DEFAULT_UI_CONFIG


def test_resolve_folds_layers_in_precedence_order() -> None:
    """Tenant → role → user, last writer wins per leaf (ADR-032 §2)."""
    tenant = {"labels": {"device": "Reader"}, "theme": {"variant": "operator"}}
    role = {"theme": {"cardStyle": "sparkline"}}
    user = {"theme": {"variant": "power"}}
    resolved = resolve_ui_config([tenant, role, user])
    assert resolved.labels == {"device": "Reader"}  # tenant, untouched below
    assert resolved.theme.variant == "power"  # user wins over tenant
    assert resolved.theme.card_style == "sparkline"  # role, untouched by user


def test_resolve_reset_to_team_default_via_absent_layer() -> None:
    """A user with no override row falls back to the tenant layer — the
    'Reset to team default' semantics (ADR-032 §2)."""
    tenant = {"theme": {"variant": "operator"}}
    assert resolve_ui_config([tenant]).theme.variant == "operator"


def test_resolve_rejects_malformed_override() -> None:
    with pytest.raises(ValidationError):
        resolve_ui_config([{"theme": {"variant": "operator", "bogus": 1}}])
