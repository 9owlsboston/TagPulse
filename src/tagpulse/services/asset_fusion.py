"""EPC -> asset multi-binding fusion lookup (Sprint 59 Track 2, item 59.10).

A real tracked item carries 2-3 RFID tags (top + two sides) so at least one face
is readable in any orientation. They are modelled as multiple active
``asset_tag_bindings`` rows for one asset (no uniqueness on ``asset_id``; the
unique index is on the *binding value*). This service resolves an observed EPC
back to its asset and groups an asset's active tags.

It backs **zone-presence** today ("this asset is here, seen via *any* of its
tags") and is **step 1 of the future positioning pipeline** (Sprint 61): the
estimator first fuses each read's EPC to an ``asset_id``, groups by asset, then
per ``(asset, antenna)`` keeps the strongest tag (the best-oriented face) before
weighting over antenna ``(x, y)``.

Scope (Sprint 59): the fusion lookup only. No RF math, no endpoint — an internal
service consumed by the existing zone/presence read paths.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from tagpulse.models.schemas import AssetTagBindingResponse


@dataclass(frozen=True)
class FusedAsset:
    """An asset resolved from a batch of observed EPCs.

    ``observed_epcs`` are the EPCs *from this batch* that resolved to the asset;
    ``active_tags`` is the asset's full active EPC tag set (the group) regardless
    of which faces were seen this batch.
    """

    asset_id: UUID
    observed_epcs: tuple[str, ...]
    active_tags: tuple[str, ...]


class _BindingReader(Protocol):
    """The narrow slice of the binding repository the fusion service needs."""

    async def get_active_by_value(
        self, tenant_id: UUID, binding_value: str
    ) -> AssetTagBindingResponse | None: ...

    async def list_active_by_values(
        self, tenant_id: UUID, values: Sequence[str]
    ) -> list[AssetTagBindingResponse]: ...

    async def list_for_asset(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        active_only: bool = False,
    ) -> list[AssetTagBindingResponse]: ...


class AssetFusionService:
    """Resolves EPCs to assets and groups an asset's tags.

    Binding history is respected by construction: every query the service issues
    filters ``unbound_at IS NULL``, so a tag rebound to a new asset resolves to
    its *current* owner and an unbound tag resolves to nothing.
    """

    def __init__(self, bindings: _BindingReader) -> None:
        self._bindings = bindings

    async def resolve_asset(self, tenant_id: UUID, epc: str) -> UUID | None:
        """Resolve a single observed EPC to its currently-bound asset, if any."""
        binding = await self._bindings.get_active_by_value(tenant_id, epc)
        return binding.asset_id if binding is not None else None

    async def active_tags(self, tenant_id: UUID, asset_id: UUID) -> list[str]:
        """Return the asset's active EPC tag group (the readable faces)."""
        bindings = await self._bindings.list_for_asset(tenant_id, asset_id, active_only=True)
        return [b.binding_value for b in bindings if b.binding_kind == "epc"]

    async def fuse(self, tenant_id: UUID, epcs: Sequence[str]) -> list[FusedAsset]:
        """Group a batch of observed EPCs by the asset each resolves to.

        Unresolvable EPCs (no active binding) are dropped. Each returned asset
        carries the EPCs from this batch that resolved to it plus its full active
        tag group. Stable ordering: assets by first-seen EPC, EPCs as given.
        """
        # Dedupe while preserving first-seen order.
        seen: dict[str, None] = {}
        for epc in epcs:
            seen.setdefault(epc, None)
        unique_epcs = list(seen)

        bindings = await self._bindings.list_active_by_values(tenant_id, unique_epcs)
        by_value = {b.binding_value: b.asset_id for b in bindings}

        grouped: dict[UUID, list[str]] = {}
        for epc in unique_epcs:
            asset_id = by_value.get(epc)
            if asset_id is None:
                continue
            grouped.setdefault(asset_id, []).append(epc)

        fused: list[FusedAsset] = []
        for asset_id, observed in grouped.items():
            tags = await self.active_tags(tenant_id, asset_id)
            fused.append(
                FusedAsset(
                    asset_id=asset_id,
                    observed_epcs=tuple(observed),
                    active_tags=tuple(tags),
                )
            )
        return fused
