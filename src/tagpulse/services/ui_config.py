"""Configurable UI — presentation-config resolution (ADR-032).

Sprint 60 increments 1–5 (ADR-032 §7 steps 1–5): the server-resolved
``GET /ui-config``, the per-user ``PUT /ui-config/me`` override layer, the
admin-set ``PUT /ui-config/{tenant,role/{role}}`` default layers, the
**label-skin** surface (increment 4 — the curated entity/nav display terms WM
asked to rename, e.g. ``Device`` → ``Reader``), and the **theme-variant**
catalogue (increment 5 — the curated persona theme + card-style values that
ride the ADR-029 design tokens, e.g. the ``sparkline`` card style).

This module owns the resolution machinery the routes/repositories only *feed*:

1. ``UiConfig`` — the schema-validated presentation document (the six leaf
   namespaces from ADR-032 §4: ``labels`` / ``theme`` / ``nav`` / ``cards`` /
   ``columns`` / ``tables``). Every leaf is presentation only — visibility,
   order, density, theme, label skins — never behaviour/semantics (the §1
   governing invariant).
2. ``LABEL_KEYS`` — the curated registry of skinnable entity/nav terms and
   their canonical default display labels (ADR-032 §4 ``labels``). It is the
   *catalogue* that makes ``labels`` a curated surface rather than a free JSON
   dump (§6.1): a ``labels`` override may only re-skin a key in this registry
   (unknown keys → ``ValidationError`` → 422). The registry is also the bottom
   layer for ``labels`` — the resolved ``GET /ui-config`` always carries the
   **complete** effective label map (defaults overlaid with overrides), so the
   UI reads one authoritative ``labels[key]`` and never re-derives defaults.
3. ``THEME_VARIANTS`` / ``CARD_STYLES`` — the curated allow-lists for the
   ``theme`` leaf (ADR-032 §4: "2–3 curated variants … not unbounded styling
   knobs"). A ``theme`` override may only select a registered persona variant
   or card style; an unknown value is rejected on write (→ ``ValidationError``
   → 422). The catalogue grows here additively — a new approved variant ships
   as data, never a fork — and the UI maps each value onto its ADR-029 tokens.
4. ``SYSTEM_DEFAULT_UI_CONFIG`` — the versioned, tested code constant that is
   the bottom layer of the merge. Every leaf but ``labels`` is intentionally
   **empty** (``theme`` = the ``default`` variant + ``default`` card style,
   nothing hidden); ``labels`` carries the canonical defaults (``LABEL_KEYS``)
   — which *are* today's UI terms, so configuring nothing still reproduces
   today's UI. ``WM_LABEL_SKIN`` is the one decided WM-facing value
   (``Device`` → ``Reader``), applied per-tenant via ``PUT /ui-config/tenant``
   (or the demo seed), **not** baked into the system default.
5. ``deep_merge`` / ``resolve_ui_config`` — the per-leaf
   System → Tenant → Role → User deep-merge engine (ADR-032 §2). Callers pass
   the layers (tenant default, role default, user override) in that order;
   last writer wins per leaf.
6. ``validate_ui_config_override`` — the ``PUT /ui-config/*`` write validator.
   It rejects unknown/ill-typed keys (``extra="forbid"`` + the ``labels`` /
   ``theme`` catalogue checks) and returns the **sparse** canonical (camelCase)
   override to persist — only the keys the caller actually set, so a one-leaf
   override still falls through to the layers below for every other leaf.
7. ``tenant_role_layers`` — splits a stored ``tenants.ui_config`` blob into its
   ``[tenant_default, role_default]`` resolve layers for a given role (the
   role layer is keyed under a reserved ``roles`` sub-object, ADR-032 §3).

Deferred to a later increment (kept out deliberately to avoid speculative
code): the ``locked`` leaf-pinning flag (ADR-032 §2) — it only earns its
complexity once the tenant/role floor layers are in real use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Reserved key inside ``tenants.ui_config`` that holds the per-role default
# layer; everything else at the top level is the tenant-default layer.
ROLES_KEY = "roles"

# Curated label-skin registry (ADR-032 §4 ``labels``, §7 step 4). Maps each
# skinnable entity/nav term to its canonical default display label. This is the
# allow-list a ``labels`` override is validated against (unknown keys rejected)
# *and* the bottom layer of the ``labels`` merge, so the resolved document
# always carries every term. Grounded in the primary entities of
# docs/data-models.md / domain-concepts-101.md. Extend here (additive) as the
# UI surfaces more skinnable terms — a new key ships as data, never a fork.
LABEL_KEYS: dict[str, str] = {
    "device": "Device",
    "telemetry": "Telemetry",
    "asset": "Asset",
    "tag": "Tag",
    "tagRead": "Tag Read",
    "zone": "Zone",
    "site": "Site",
    "lot": "Lot",
    "stockItem": "Stock Item",
    "alert": "Alert",
    "rule": "Rule",
}

# The one WM-facing label value decided for Sprint 60 (ADR-032 §4: ``Device`` →
# ``Reader``; the ``Telemetry`` rename stayed TBD with WM, so it is *not*
# skinned here). Sparse on purpose — every other term falls through to its
# canonical default. Applied per-tenant via ``PUT /ui-config/tenant`` or the
# demo seed; never baked into the system default.
WM_LABEL_SKIN: dict[str, str] = {"device": "Reader"}

# The concrete WM-facing presentation values for the demo tenant (ADR-032 §4).
# This is the demo's *interpretation* of the June 2026 WM focus-group asks
# ("rename Device→Reader, hide plumbing columns, simplify nav, hide unused
# cards, sparkline visuals"), expressed against the real nav-section / card /
# page keys the UI uses. It is **demo seed data, not a product default** — every
# value here is a per-tenant override a real WM rollout would tune; nothing is
# baked into ``SYSTEM_DEFAULT_UI_CONFIG``. Keys must stay in lock-step with the
# UI registries (``src/lib/nav.tsx`` section keys, ``Dashboard.tsx`` tile ids,
# the per-page ``columns``/``tables`` page names).
#   - ``labels``  — the decided ``Device``→``Reader`` skin.
#   - ``nav``     — hide the operator-irrelevant "Data Management" section
#                   (tag import / reconciliation / data mappings — pure plumbing
#                   for a floor operator).
#   - ``cards``   — hide the three technical dashboard cards a floor operator
#                   doesn't action (raw reads/hour throughput, the tag registry
#                   count, and the reconciliation backlog).
#   - ``theme``   — the sparkline card style WM asked for (rides ADR-029 tokens).
#   - ``columns`` — TID + raw user-memory default-OFF on the Tag Reads page
#                   (the "hide plumbing columns" ask); the page already defaults
#                   these to advanced, so this is belt-and-suspenders + the
#                   explicit record of the WM keep/cut list.
#   - ``tables``  — newest reads first on the Tag Reads page (sort-by-header
#                   default ask).
WM_DEMO_PRESENTATION: dict[str, Any] = {
    "labels": dict(WM_LABEL_SKIN),
    "nav": {"hidden": ["sec-data-management"]},
    "cards": {"dashboard": {"hidden": ["reads-per-hour", "tags", "recon-backlog"]}},
    "theme": {"cardStyle": "sparkline"},
    "columns": {"tag_reads": {"advanced": ["tid", "user_memory_hex"]}},
    "tables": {"tag_reads": {"defaultSort": {"key": "timestamp", "dir": "desc"}}},
}

# Curated theme catalogue (ADR-032 §4 ``theme``, §7 step 5). The ``theme`` leaf
# rides the ADR-029 design tokens: a small allow-list of approved persona
# *variants* and card *styles*, not unbounded styling knobs. A ``theme``
# override may only select a registered value (unknown → ``ValidationError`` →
# 422); the UI maps each value onto its token set. Both catalogues are additive
# — a new approved variant/style ships as a tuple entry, never a fork — and
# both lead with ``"default"`` (today's UI), which is the system default.
THEME_VARIANTS: tuple[str, ...] = ("default", "operator", "power")
CARD_STYLES: tuple[str, ...] = ("default", "sparkline")


class _Leaf(BaseModel):
    """Base for every config node.

    ``extra="forbid"`` makes the document a curated surface, not a free JSON
    dump (ADR-032 §6.1) — unknown keys are rejected on validation.
    ``populate_by_name`` lets the camelCase wire keys (``cardStyle``,
    ``defaultSort``) round-trip while the Python attributes stay snake_case.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ThemeConfig(_Leaf):
    """Theme variant + card style, riding the ADR-029 design tokens.

    Both fields are curated surfaces (ADR-032 §4): ``variant`` must name a
    registered persona theme (``THEME_VARIANTS``) and ``card_style`` a
    registered card visual (``CARD_STYLES``). The default is today's UI
    (``default`` / ``default``).
    """

    variant: str = "default"
    card_style: str = Field(default="default", alias="cardStyle")

    @field_validator("variant")
    @classmethod
    def _variant_is_registered(cls, value: str) -> str:
        if value not in THEME_VARIANTS:
            raise ValueError(f"unknown theme variant: {value!r}")
        return value

    @field_validator("card_style")
    @classmethod
    def _card_style_is_registered(cls, value: str) -> str:
        if value not in CARD_STYLES:
            raise ValueError(f"unknown card style: {value!r}")
        return value


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
    ``"assets"``, ``"tag_reads"``); ``labels`` is the display-label skin — a
    map of curated term keys (``LABEL_KEYS``) to display strings. ``labels``
    defaults to the full canonical registry so the resolved document always
    carries every term (the UI reads one authoritative ``labels[key]``).
    """

    labels: dict[str, str] = Field(default_factory=lambda: dict(LABEL_KEYS))
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    nav: NavConfig = Field(default_factory=NavConfig)
    cards: dict[str, CardGroup] = Field(default_factory=dict)
    columns: dict[str, ColumnGroup] = Field(default_factory=dict)
    tables: dict[str, TableConfig] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def _labels_are_registered(cls, value: dict[str, str]) -> dict[str, str]:
        """Curate the label surface (ADR-032 §6.1): only registered term keys
        may be skinned, so a typo'd or behaviour-smuggling key is rejected on
        write (→ ``ValidationError`` → 422) rather than silently stored."""
        unknown = sorted(set(value) - set(LABEL_KEYS))
        if unknown:
            raise ValueError(f"unknown label key(s): {', '.join(unknown)}")
        return value


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
