"""Unit tests for the Configurable UI resolver + schema (Sprint 60, ADR-032).

Increment 1 (ADR-032 §7 step 1): system defaults only. These tests pin the
schema (curated, presentation-only, camelCase wire keys) and the per-leaf
deep-merge engine the later increments feed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.services.ui_config import (
    CARD_STYLES,
    LABEL_KEYS,
    ROLES_KEY,
    SYSTEM_DEFAULT_UI_CONFIG,
    THEME_VARIANTS,
    WM_LABEL_SKIN,
    ColumnGroup,
    ThemeConfig,
    UiConfig,
    deep_merge,
    resolve_ui_config,
    tenant_role_layers,
    validate_ui_config_override,
)

# -- system default --------------------------------------------------------


def test_system_default_is_empty_today_ui() -> None:
    """Configuring nothing reproduces today's UI: no skins beyond the canonical
    label defaults, default theme, nothing hidden (ADR-032 §3, §7 step 1)."""
    cfg = SYSTEM_DEFAULT_UI_CONFIG
    # ``labels`` carries the canonical registry defaults — which *are* today's
    # UI terms ("Device", "Telemetry", …), so this is still the unchanged UI.
    assert cfg.labels == LABEL_KEYS
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
    assert resolved.labels["device"] == "Reader"  # tenant skin, untouched below
    assert resolved.labels["telemetry"] == "Telemetry"  # canonical default falls through
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


# -- validate_ui_config_override (write path, increment 2) ------------------


def test_validate_override_keeps_only_set_keys() -> None:
    """The stored override is sparse so untouched leaves fall through (ADR-032
    §2). Setting one nested key must not materialise sibling defaults."""
    out = validate_ui_config_override({"theme": {"cardStyle": "sparkline"}})
    assert out == {"theme": {"cardStyle": "sparkline"}}


def test_validate_override_normalises_to_camelcase() -> None:
    """snake_case input is accepted (``populate_by_name``) but persisted as the
    canonical camelCase wire key."""
    out = validate_ui_config_override({"theme": {"card_style": "sparkline"}})
    assert out == {"theme": {"cardStyle": "sparkline"}}


def test_validate_override_empty_is_empty() -> None:
    assert validate_ui_config_override({}) == {}


def test_validate_override_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        validate_ui_config_override({"bogus": 1})


def test_validate_override_rejects_bad_leaf_type() -> None:
    with pytest.raises(ValidationError):
        validate_ui_config_override(
            {"tables": {"assets": {"defaultSort": {"key": "name", "dir": "up"}}}}
        )


def test_validate_override_round_trips_through_resolve() -> None:
    """A persisted override folds back cleanly as the user layer."""
    override = validate_ui_config_override({"columns": {"assets": {"advanced": ["tid"]}}})
    resolved = resolve_ui_config([override])
    assert resolved.columns["assets"].advanced == ["tid"]


# -- tenant_role_layers (increment 3) --------------------------------------


def test_tenant_role_layers_none_is_empty() -> None:
    assert tenant_role_layers(None, "viewer") == []
    assert tenant_role_layers({}, "viewer") == []


def test_tenant_role_layers_tenant_only() -> None:
    """Top-level (non-``roles``) keys form the single tenant-default layer."""
    stored = {"labels": {"device": "Reader"}, "theme": {"variant": "operator"}}
    assert tenant_role_layers(stored, "viewer") == [stored]


def test_tenant_role_layers_splits_tenant_then_role() -> None:
    """The blob splits into [tenant-default, role-default] in precedence order."""
    stored = {
        "theme": {"variant": "operator"},
        ROLES_KEY: {"viewer": {"theme": {"cardStyle": "sparkline"}}},
    }
    assert tenant_role_layers(stored, "viewer") == [
        {"theme": {"variant": "operator"}},
        {"theme": {"cardStyle": "sparkline"}},
    ]


def test_tenant_role_layers_role_absent() -> None:
    """A role with no layer contributes nothing beyond the tenant default."""
    stored = {
        "theme": {"variant": "operator"},
        ROLES_KEY: {"editor": {"theme": {"cardStyle": "sparkline"}}},
    }
    assert tenant_role_layers(stored, "viewer") == [{"theme": {"variant": "operator"}}]


def test_tenant_role_layers_role_only() -> None:
    """A blob with only a ``roles`` sub-object yields just the role layer."""
    stored = {ROLES_KEY: {"viewer": {"theme": {"cardStyle": "sparkline"}}}}
    assert tenant_role_layers(stored, "viewer") == [{"theme": {"cardStyle": "sparkline"}}]


def test_tenant_role_layers_empty_role_layer_omitted() -> None:
    stored = {"theme": {"variant": "operator"}, ROLES_KEY: {"viewer": {}}}
    assert tenant_role_layers(stored, "viewer") == [{"theme": {"variant": "operator"}}]


def test_tenant_role_layers_round_trip_through_resolve() -> None:
    """Folding tenant + role + user shows per-leaf precedence (ADR-032 §2)."""
    stored = {
        "theme": {"variant": "operator", "cardStyle": "default"},
        ROLES_KEY: {"viewer": {"theme": {"cardStyle": "sparkline"}}},
    }
    layers = tenant_role_layers(stored, "viewer")
    user = {"theme": {"variant": "power"}}
    resolved = resolve_ui_config([*layers, user])
    assert resolved.theme.variant == "power"  # user wins
    assert resolved.theme.card_style == "sparkline"  # role wins over tenant


# -- label skins (increment 4) ---------------------------------------------


def test_system_default_labels_are_the_registry() -> None:
    """The resolved system default carries the full canonical label catalogue,
    so the UI reads one authoritative ``labels[key]`` (ADR-032 §7 step 4)."""
    dumped = SYSTEM_DEFAULT_UI_CONFIG.model_dump(by_alias=True)
    assert dumped["labels"] == LABEL_KEYS
    assert dumped["labels"]["device"] == "Device"
    assert dumped["labels"]["telemetry"] == "Telemetry"


def test_label_override_reskins_one_term_keeps_the_rest() -> None:
    """A sparse label override re-skins its keys; every other term falls
    through to the canonical default (the per-leaf merge, ADR-032 §2)."""
    resolved = resolve_ui_config([{"labels": {"device": "Reader"}}])
    assert resolved.labels["device"] == "Reader"
    assert resolved.labels["telemetry"] == "Telemetry"
    # the full catalogue is still present
    assert set(resolved.labels) == set(LABEL_KEYS)


def test_wm_label_skin_resolves() -> None:
    """The decided WM value (Device → Reader) applied as a tenant default
    resolves through (ADR-032 §4)."""
    resolved = resolve_ui_config([{"labels": WM_LABEL_SKIN}])
    assert resolved.labels["device"] == "Reader"
    assert WM_LABEL_SKIN == {"device": "Reader"}  # sparse: only the decided term


def test_wm_demo_presentation_is_valid_and_resolves() -> None:
    """The concrete WM demo presentation (label skin + nav/cards/theme/columns/
    tables) is a *valid* override that resolves through the real merge — so the
    demo seed can never push a doc the schema would 422 on (ADR-032 §4)."""
    from tagpulse.services.ui_config import WM_DEMO_PRESENTATION

    # It passes the write-path validator (extra="forbid" + curated catalogues).
    validate_ui_config_override(WM_DEMO_PRESENTATION)

    resolved = resolve_ui_config([WM_DEMO_PRESENTATION])
    # Each consumed leaf folds through to the resolved document.
    assert resolved.labels["device"] == "Reader"
    assert "sec-data-management" in resolved.nav.hidden
    assert "reads-per-hour" in resolved.cards["dashboard"].hidden
    assert resolved.theme.card_style == "sparkline"
    assert resolved.columns["tag_reads"].advanced == ["tid", "user_memory_hex"]
    assert resolved.tables["tag_reads"].default_sort is not None
    assert resolved.tables["tag_reads"].default_sort.key == "timestamp"
    assert resolved.tables["tag_reads"].default_sort.dir == "desc"


def test_unknown_label_key_rejected_on_validate() -> None:
    """``labels`` is a curated surface — an unregistered key is rejected
    (ADR-032 §6.1)."""
    with pytest.raises(ValidationError):
        UiConfig.model_validate({"labels": {"gadget": "Thing"}})


def test_validate_override_rejects_unknown_label_key() -> None:
    with pytest.raises(ValidationError):
        validate_ui_config_override({"labels": {"gadget": "Thing"}})


def test_validate_override_keeps_label_skin_sparse() -> None:
    """A label override persists only the re-skinned keys, not the whole
    catalogue, so it still falls through for every other term."""
    out = validate_ui_config_override({"labels": {"device": "Reader"}})
    assert out == {"labels": {"device": "Reader"}}


# -- theme variants (increment 5) ------------------------------------------


def test_theme_catalogue_includes_the_default() -> None:
    """Both catalogues lead with today's UI value (ADR-032 §7 step 5)."""
    assert "default" in THEME_VARIANTS
    assert "default" in CARD_STYLES
    assert SYSTEM_DEFAULT_UI_CONFIG.theme.variant == "default"
    assert SYSTEM_DEFAULT_UI_CONFIG.theme.card_style == "default"


def test_registered_theme_values_accepted() -> None:
    """Every catalogued variant / card style validates."""
    for variant in THEME_VARIANTS:
        assert ThemeConfig.model_validate({"variant": variant}).variant == variant
    for style in CARD_STYLES:
        theme = ThemeConfig.model_validate({"cardStyle": style})
        assert theme.card_style == style


def test_unknown_theme_variant_rejected() -> None:
    """``theme`` is a curated surface — an unregistered variant is rejected
    (ADR-032 §4: '2–3 curated variants … not unbounded styling knobs')."""
    with pytest.raises(ValidationError):
        ThemeConfig.model_validate({"variant": "rainbow"})


def test_unknown_card_style_rejected() -> None:
    with pytest.raises(ValidationError):
        ThemeConfig.model_validate({"cardStyle": "neon"})


def test_validate_override_rejects_unknown_theme_value() -> None:
    with pytest.raises(ValidationError):
        validate_ui_config_override({"theme": {"variant": "rainbow"}})


def test_theme_override_resolves_and_stays_sparse() -> None:
    """A sparse theme override re-skins its key; the sibling falls through
    to the default, and the persisted override carries only the set key."""
    resolved = resolve_ui_config([{"theme": {"cardStyle": "sparkline"}}])
    assert resolved.theme.card_style == "sparkline"
    assert resolved.theme.variant == "default"  # sibling falls through
    out = validate_ui_config_override({"theme": {"cardStyle": "sparkline"}})
    assert out == {"theme": {"cardStyle": "sparkline"}}
