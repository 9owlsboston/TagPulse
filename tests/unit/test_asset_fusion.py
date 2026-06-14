"""Unit tests for the EPC->asset fusion lookup (Sprint 59 Track 2, 59.10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.models.schemas import AssetTagBindingResponse
from tagpulse.services.asset_fusion import AssetFusionService, FusedAsset


def _binding(
    tenant_id: UUID,
    asset_id: UUID,
    binding_value: str,
    *,
    binding_kind: str = "epc",
    unbound: bool = False,
) -> AssetTagBindingResponse:
    return AssetTagBindingResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        asset_id=asset_id,
        binding_value=binding_value,
        binding_kind=binding_kind,
        bound_at=datetime.now(UTC),
        unbound_at=datetime.now(UTC) if unbound else None,
        metadata=None,
    )


class _FakeBindingRepo:
    """In-memory binding store honouring the active/unbound semantics."""

    def __init__(self, bindings: list[AssetTagBindingResponse]) -> None:
        self._bindings = bindings

    async def get_active_by_value(
        self, tenant_id: UUID, binding_value: str
    ) -> AssetTagBindingResponse | None:
        for b in self._bindings:
            if (
                b.tenant_id == tenant_id
                and b.binding_value == binding_value
                and b.unbound_at is None
            ):
                return b
        return None

    async def list_active_by_values(
        self, tenant_id: UUID, values: Any
    ) -> list[AssetTagBindingResponse]:
        wanted = set(values)
        return [
            b
            for b in self._bindings
            if b.tenant_id == tenant_id and b.binding_value in wanted and b.unbound_at is None
        ]

    async def list_for_asset(
        self,
        tenant_id: UUID,
        asset_id: UUID,
        *,
        active_only: bool = False,
    ) -> list[AssetTagBindingResponse]:
        out = [b for b in self._bindings if b.tenant_id == tenant_id and b.asset_id == asset_id]
        if active_only:
            out = [b for b in out if b.unbound_at is None]
        return out


@pytest.mark.asyncio
async def test_resolve_single_tag_asset() -> None:
    tenant, asset = uuid4(), uuid4()
    repo = _FakeBindingRepo([_binding(tenant, asset, "EPC-A")])
    svc = AssetFusionService(repo)

    assert await svc.resolve_asset(tenant, "EPC-A") == asset
    assert await svc.resolve_asset(tenant, "EPC-UNKNOWN") is None


@pytest.mark.asyncio
async def test_three_tag_item_resolves_from_any_face() -> None:
    """A 3-tag item resolves to the same asset from any of its EPCs."""
    tenant, asset = uuid4(), uuid4()
    repo = _FakeBindingRepo(
        [
            _binding(tenant, asset, "EPC-TOP"),
            _binding(tenant, asset, "EPC-SIDE1"),
            _binding(tenant, asset, "EPC-SIDE2"),
        ]
    )
    svc = AssetFusionService(repo)

    for face in ("EPC-TOP", "EPC-SIDE1", "EPC-SIDE2"):
        assert await svc.resolve_asset(tenant, face) == asset

    tags = await svc.active_tags(tenant, asset)
    assert set(tags) == {"EPC-TOP", "EPC-SIDE1", "EPC-SIDE2"}


@pytest.mark.asyncio
async def test_rebound_tag_resolves_to_current_owner() -> None:
    """A tag unbound from one asset and rebound to another resolves to the new owner."""
    tenant, old_asset, new_asset = uuid4(), uuid4(), uuid4()
    repo = _FakeBindingRepo(
        [
            _binding(tenant, old_asset, "EPC-ROAM", unbound=True),
            _binding(tenant, new_asset, "EPC-ROAM"),
        ]
    )
    svc = AssetFusionService(repo)

    assert await svc.resolve_asset(tenant, "EPC-ROAM") == new_asset
    # The old owner no longer groups the roamed tag.
    assert await svc.active_tags(tenant, old_asset) == []
    assert await svc.active_tags(tenant, new_asset) == ["EPC-ROAM"]


@pytest.mark.asyncio
async def test_active_tags_excludes_unbound_and_non_epc() -> None:
    tenant, asset = uuid4(), uuid4()
    repo = _FakeBindingRepo(
        [
            _binding(tenant, asset, "EPC-LIVE"),
            _binding(tenant, asset, "EPC-GONE", unbound=True),
            _binding(tenant, asset, "TID-XYZ", binding_kind="tid"),
        ]
    )
    svc = AssetFusionService(repo)

    assert await svc.active_tags(tenant, asset) == ["EPC-LIVE"]


@pytest.mark.asyncio
async def test_fuse_groups_batch_by_asset() -> None:
    tenant, asset_a, asset_b = uuid4(), uuid4(), uuid4()
    repo = _FakeBindingRepo(
        [
            _binding(tenant, asset_a, "A-TOP"),
            _binding(tenant, asset_a, "A-SIDE"),
            _binding(tenant, asset_b, "B-TOP"),
        ]
    )
    svc = AssetFusionService(repo)

    # Batch: both faces of A, one face of B, a duplicate, and an unknown EPC.
    fused = await svc.fuse(tenant, ["A-TOP", "B-TOP", "A-SIDE", "A-TOP", "GHOST"])

    by_id = {f.asset_id: f for f in fused}
    assert set(by_id) == {asset_a, asset_b}

    fa = by_id[asset_a]
    assert isinstance(fa, FusedAsset)
    assert fa.observed_epcs == ("A-TOP", "A-SIDE")  # deduped, first-seen order
    assert set(fa.active_tags) == {"A-TOP", "A-SIDE"}

    fb = by_id[asset_b]
    assert fb.observed_epcs == ("B-TOP",)
    assert set(fb.active_tags) == {"B-TOP"}


@pytest.mark.asyncio
async def test_fuse_empty_batch_returns_empty() -> None:
    tenant = uuid4()
    svc = AssetFusionService(_FakeBindingRepo([]))
    assert await svc.fuse(tenant, []) == []


@pytest.mark.asyncio
async def test_fuse_respects_tenant_isolation() -> None:
    tenant_a, tenant_b, asset = uuid4(), uuid4(), uuid4()
    repo = _FakeBindingRepo([_binding(tenant_a, asset, "EPC-X")])
    svc = AssetFusionService(repo)

    assert await svc.resolve_asset(tenant_b, "EPC-X") is None
    assert await svc.fuse(tenant_b, ["EPC-X"]) == []
