"""Configurable UI — presentation-config resolution (ADR-032).

Sprint 60 increment 1 (ADR-032 §7 step 1): the server-resolved
``GET /ui-config`` over **system defaults only** — no persistence yet.

This module owns three things the later increments only *feed*:

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
   System → Tenant → Role → User deep-merge engine (ADR-032 §2). Increment 1
   resolves the system default only; increments 2–3 add ``user_ui_prefs`` and
   ``tenants.ui_config`` as override layers without touching this contract.

Deferred to later increments (kept out deliberately to avoid speculative
code): the ``locked`` leaf-pinning flag (ADR-032 §2) only has meaning once the
tenant/role layers exist, so it lands with increment 3; write validation for
``PUT /ui-config/*`` lands with increment 2.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
