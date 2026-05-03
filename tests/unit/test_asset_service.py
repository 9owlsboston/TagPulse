"""Unit tests for AssetService (Sprint 15 Phase B)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.asset_service import AssetService
from tagpulse.models.schemas import (
    AssetCreate,
    AssetResponse,
    AssetTagBindingCreate,
    AssetTagBindingResponse,
    AssetUpdate,
)


def _asset(tenant_id: UUID, **overrides: Any) -> AssetResponse:
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        external_ref=None,
        name="Pallet-1",
        asset_type="pallet",
        status="active",
        parent_asset_id=None,
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return AssetResponse(**base)


def _binding(
    tenant_id: UUID, asset_id: UUID, **overrides: Any
) -> AssetTagBindingResponse:
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        asset_id=asset_id,
        binding_value="E280-1234",
        binding_kind="epc",
        bound_at=datetime.now(UTC),
        unbound_at=None,
        metadata=None,
    )
    base.update(overrides)
    return AssetTagBindingResponse(**base)


class _FakeAssetRepo:
    def __init__(self) -> None:
        self.next_response: Any = None

    async def create(self, tenant_id: UUID, payload: AssetCreate) -> AssetResponse:
        return self.next_response or _asset(
            tenant_id, name=payload.name, asset_type=payload.asset_type
        )

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        return self.next_response

    async def list(  # type: ignore[no-untyped-def]
        self, tenant_id, *, asset_type=None, status=None, q=None, limit=100, offset=0
    ):
        return self.next_response or []

    async def update(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, patch
    ):
        return self.next_response

    async def delete(self, tenant_id: UUID, asset_id: UUID) -> bool:
        return bool(self.next_response)


class _FakeBindingRepo:
    def __init__(self) -> None:
        self.next_response: Any = None
        self.collisions = 0

    async def create(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, payload
    ):
        return self.next_response or _binding(
            tenant_id, asset_id, binding_value=payload.binding_value,
            binding_kind=payload.binding_kind,
        )

    async def list_for_asset(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, *, active_only=False
    ):
        return self.next_response or []

    async def unbind(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, binding_value
    ):
        return bool(self.next_response)

    async def get_active_by_value(  # type: ignore[no-untyped-def]
        self, tenant_id, binding_value
    ):
        return self.next_response

    async def count_other_tenant_collisions(  # type: ignore[no-untyped-def]
        self, tenant_id, binding_value
    ):
        return self.collisions


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(  # type: ignore[no-untyped-def]
        self, tenant_id, action, resource_type, resource_id, changes=None,
        *, user_id=None,
    ):
        self.entries.append(
            {"action": action, "resource_type": resource_type,
             "resource_id": resource_id, "changes": changes, "user_id": user_id}
        )


def _service() -> tuple[AssetService, _FakeAssetRepo, _FakeBindingRepo, _FakeAudit]:
    a, b, audit = _FakeAssetRepo(), _FakeBindingRepo(), _FakeAudit()
    svc = AssetService(asset_repo=a, binding_repo=b, audit=audit)  # type: ignore[arg-type]
    return svc, a, b, audit


@pytest.mark.asyncio
async def test_create_asset_writes_audit() -> None:
    svc, _, _, audit = _service()
    out = await svc.create_asset(
        uuid4(), uuid4(), AssetCreate(name="Bin-A", asset_type="bin")
    )
    assert out.name == "Bin-A"
    assert audit.entries[-1]["action"] == "asset.created"
    assert audit.entries[-1]["changes"]["asset_type"] == "bin"


@pytest.mark.asyncio
async def test_retire_asset_only_audits_when_deleted() -> None:
    svc, asset_repo, _, audit = _service()
    asset_repo.next_response = True
    assert await svc.retire_asset(uuid4(), uuid4(), uuid4()) is True
    assert audit.entries[-1]["action"] == "asset.retired"


@pytest.mark.asyncio
async def test_retire_missing_asset_no_audit() -> None:
    svc, asset_repo, _, audit = _service()
    asset_repo.next_response = False
    assert await svc.retire_asset(uuid4(), uuid4(), uuid4()) is False
    assert audit.entries == []


@pytest.mark.asyncio
async def test_bind_tag_audits_with_value_and_kind() -> None:
    svc, _, _, audit = _service()
    aid = uuid4()
    out = await svc.bind_tag(
        uuid4(), uuid4(), aid,
        AssetTagBindingCreate(binding_value="X1", binding_kind="tid"),
    )
    assert out.binding_value == "X1"
    assert audit.entries[-1]["action"] == "asset.bound"
    assert audit.entries[-1]["changes"] == {
        "binding_value": "X1", "binding_kind": "tid"
    }


@pytest.mark.asyncio
async def test_unbind_tag_audits_only_when_unbound() -> None:
    svc, _, binding_repo, audit = _service()
    binding_repo.next_response = True
    assert await svc.unbind_tag(uuid4(), uuid4(), uuid4(), "X1") is True
    assert audit.entries[-1]["action"] == "asset.unbound"
    audit.entries.clear()
    binding_repo.next_response = False
    assert await svc.unbind_tag(uuid4(), uuid4(), uuid4(), "X1") is False
    assert audit.entries == []


@pytest.mark.asyncio
async def test_count_other_tenant_collisions_increments_counter() -> None:
    svc, _, binding_repo, _ = _service()
    binding_repo.collisions = 3
    assert await svc.count_other_tenant_collisions(uuid4(), "E280-1234") == 3


@pytest.mark.asyncio
async def test_update_asset_returns_none_when_missing() -> None:
    svc, asset_repo, _, audit = _service()
    asset_repo.next_response = None
    assert await svc.update_asset(uuid4(), uuid4(), uuid4(), AssetUpdate(name="x")) is None
    assert audit.entries == []


@pytest.mark.asyncio
async def test_get_active_binding_delegates() -> None:
    svc, _, binding_repo, _ = _service()
    expected = _binding(uuid4(), uuid4())
    binding_repo.next_response = expected
    out = await svc.get_active_binding(uuid4(), "E280-1234")
    assert out is expected
