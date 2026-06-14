"""Configurable UI — presentation-config resolution (ADR-032).

Sprint 60 increments 1–3 (ADR-032 §7 steps 1–3): the server-resolved
``GET /ui-config``, the per-user ``PUT /ui-config/me`` override layer, and the
admin-set ``PUT /ui-config/{tenant,role/{role}}`` default layers.

This module owns the resolution machinery the routes/repositories only *feed*:

1. ``UiConfig`` — the schema-validated presentation document (the six leaf
   namespaces from ADR-032 §4: ``labels`` / ``theme`` / ``nav`` / ``cards`` /
   ``columns`` / ``tables``). Every leaf is presentation only — visibility,
   order, density, theme, label skins — never behaviour/semantics (the §1
   governing invariant).
2. ``SYSTEM_DEFAULT_UI_CONFIG`` — the versioned, tested code constant that is
   the bottom layer of the merge. It is intentionally **empty** (no label
   skins, default theme, nothing hidden): configuring nothing reproduces
   today's UI byte-for-byte. The concrete WM-facing label values are chosen
   in the terminology sprint, not here (ADR-032 "out of scope").
3. ``deep_merge`` / ``resolve_ui_config`` — the per-leaf
   System → Tenant → Role → User deep-merge engine (ADR-032 §2). Callers pass
   the layers (tenant default, role default, user override) in that order;
   last writer wins per leaf.
4. ``validate_ui_config_override`` — the ``PUT /ui-config/*`` write validator.
   It rejects unknown/ill-typed keys (``extra="forbid"``) and returns the
   **sparse** canonical (camelCase) override to persist — only the keys the
   caller actually set, so a one-leaf override still falls through to the
   layers below for every other leaf.
5. ``tenant_role_layers`` — splits a stored ``tenants.ui_config`` blob into its
   ``[tenant_default, role_default]`` resolve layers for a given role (the
   role layer is keyed under a reserved ``roles`` sub-object, ADR-032 §3).

Deferred to a later increment (kept out deliberately to avoid speculative
code): the ``locked`` leaf-pinning flag (ADR-032 §2) — it only earns its
complexity once the tenant/role floor layers are in real use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Reserved key inside ``tenants.ui_config`` that holds the per-role default
# layer; everything else at the top level is the tenant-default layer.
ROLES_KEY = "roles"


class _Leaf(BaseModel):
    """Base for every config node.

    ``extra="forbid"`` makes the document a curated surface, not a free JSON
    dump (ADR-032 §6.1) — unknown keys are rejected on validation.
    ``populate_by_name`` lets the camelCase wire keys (``cardStyle``,
    ``defaultSort``) round-trip while the Python attributes stay snake_case.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ThemeConfig(_Leaf):
    """Theme variant + card style, riding the ADR-029 design tokens."""

    variant: str = "default"
    card_style: str = Field(default="default", alias="cardStyle")


class NavConfig(_Leaf):
    """Sidebar/nav section visibility + ordering (the menu system)."""

    hidden: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)


class CardGroup(_Leaf):
    """Per-page dashboard-card visibility + ordering."""

    hidden: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)


class ColumnGroup(_Leaf):
    """Per-page list-column config (ADR-030 surface).

    ``advanced`` is the key move for the TID / ``metadata``-JSONB ask: those
    columns are default-OFF, revealed by an "Advanced columns" toggle. This is
    *default-hidden*, never deletion — the field still exists in the API and
    exports (ADR-032 §4, §6.3).
    """

    hidden: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    advanced: list[str] = Field(default_factory=list)


class SortSpec(_Leaf):
    """A default sort for a list page (ADR-030 sort-by-header default)."""

    key: str
    dir: Literal["asc", "desc"] = "asc"


class TableConfig(_Leaf):
    """Per-page table defaults (the persisted default sort)."""

    default_sort: SortSpec | None = Field(default=None, alias="defaultSort")


class UiConfig(_Leaf):
    """The resolved presentation-config document served by ``GET /ui-config``.

    ``cards`` / ``columns`` / ``tables`` are keyed by page name (e.g.
    ``"assets"``, ``"tag_reads"``); ``labels`` is a free display-label skin
    (e.g. ``{"device": "Reader"}``).
    """

    labels: dict[str, str] = Field(default_factory=dict)
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    nav: NavConfig = Field(default_factory=NavConfig)
    cards: dict[str, CardGroup] = Field(default_factory=dict)
    columns: dict[str, ColumnGroup] = Field(default_factory=dict)
    tables: dict[str, TableConfig] = Field(default_factory=dict)


# The bottom merge layer. Empty = today's UI unchanged (ADR-032 §3, §7 step 1).
SYSTEM_DEFAULT_UI_CONFIG = UiConfig()


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Per-leaf deep-merge (ADR-032 §2): ``override`` wins per key.

    Nested dicts recurse so a layer that sets one leaf inherits every other
    leaf from below; lists and scalars replace wholesale (a list *is* a leaf).
    Neither input is mutated.
    """
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = value
    return result


def resolve_ui_config(overrides: Sequence[Mapping[str, Any]] = ()) -> UiConfig:
    """Resolve the effective config by folding ``overrides`` onto the system
    default in precedence order (ADR-032 §2, §5).

    Increment 1 (ADR-032 §7 step 1) passes no overrides, so every caller gets
    the system default. Later increments pass ``[tenant, role, user]`` in that
    order — last writer wins per leaf. The result is re-validated through
    :class:`UiConfig`, so a malformed override layer is caught here rather than
    reaching the UI.
    """
    merged: dict[str, Any] = SYSTEM_DEFAULT_UI_CONFIG.model_dump(by_alias=True)
    for layer in overrides:
        merged = deep_merge(merged, layer)
    return UiConfig.model_validate(merged)


def validate_ui_config_override(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ``PUT /ui-config/*`` body and return the sparse override doc.

    Runs the payload through :class:`UiConfig` so unknown keys and ill-typed
    leaves are rejected (``extra="forbid"``; raises ``pydantic.ValidationError``
    → the route maps it to 422), then returns **only the keys the caller set**
    via ``model_dump(exclude_unset=True)``. Keeping the override sparse is what
    makes the per-layer deep-merge work: a user who hides one column must still
    inherit every other leaf from role/tenant/system, so we must not
    materialise defaults for the keys they left untouched. The returned doc is
    canonical camelCase (``by_alias=True``) for stable storage.
    """
    model = UiConfig.model_validate(payload)
    return model.model_dump(by_alias=True, exclude_unset=True)


def tenant_role_layers(stored: Mapping[str, Any] | None, role: str) -> list[dict[str, Any]]:
    """Split a stored ``tenants.ui_config`` blob into resolve layers.

    Returns ``[tenant_default, role_default]`` (ADR-032 §2–§3 order), omitting
    either if empty. The tenant-default layer is every top-level leaf *except*
    the reserved ``roles`` sub-object; the role-default layer is
    ``stored["roles"][role]`` when present. ``None``/empty input yields no
    layers, so a tenant with no ``ui_config`` falls straight through to the
    system default. Each layer is itself sparse, so untouched leaves fall
    through to the layer below (the per-leaf merge invariant).
    """
    if not stored:
        return []
    layers: list[dict[str, Any]] = []
    tenant_layer = {k: v for k, v in stored.items() if k != ROLES_KEY}
    if tenant_layer:
        layers.append(tenant_layer)
    roles = stored.get(ROLES_KEY)
    if isinstance(roles, Mapping):
        role_layer = roles.get(role)
        if isinstance(role_layer, Mapping) and role_layer:
            layers.append(dict(role_layer))
    return layers
