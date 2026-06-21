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
        status="active",
        parent_asset_id=None,
        category_id=uuid4(),
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return AssetResponse(**base)


def _binding(tenant_id: UUID, asset_id: UUID, **overrides: Any) -> AssetTagBindingResponse:
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
        self.last_list_kwargs: dict[str, Any] | None = None

    async def create(self, tenant_id: UUID, payload: AssetCreate) -> AssetResponse:
        return self.next_response or _asset(
            tenant_id, name=payload.name, category_id=payload.category_id
        )

    async def get(self, tenant_id: UUID, asset_id: UUID) -> AssetResponse | None:
        return self.next_response

    async def list(  # type: ignore[no-untyped-def]
        self,
        tenant_id,
        *,
        status=None,
        statuses=None,
        category_ids=None,
        q=None,
        labels=None,
        sort=None,
        order="desc",
        limit=100,
        offset=0,
    ):
        self.last_list_kwargs = {
            "tenant_id": tenant_id,
            "status": status,
            "statuses": statuses,
            "category_ids": category_ids,
            "q": q,
            "labels": labels,
            "sort": sort,
            "order": order,
            "limit": limit,
            "offset": offset,
        }
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
            tenant_id,
            asset_id,
            binding_value=payload.binding_value,
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
        self,
        tenant_id,
        action,
        resource_type,
        resource_id,
        changes=None,
        *,
        user_id=None,
    ):
        self.entries.append(
            {
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "changes": changes,
                "user_id": user_id,
            }
        )


def _service() -> tuple[AssetService, _FakeAssetRepo, _FakeBindingRepo, _FakeAudit]:
    a, b, audit = _FakeAssetRepo(), _FakeBindingRepo(), _FakeAudit()
    svc = AssetService(asset_repo=a, binding_repo=b, audit=audit)  # type: ignore[arg-type]
    return svc, a, b, audit


@pytest.mark.asyncio
async def test_create_asset_writes_audit() -> None:
    svc, _, _, audit = _service()
    cid = uuid4()
    out = await svc.create_asset(uuid4(), uuid4(), AssetCreate(name="Bin-A", category_id=cid))
    assert out.name == "Bin-A"
    assert audit.entries[-1]["action"] == "asset.created"
    assert audit.entries[-1]["changes"]["category_id"] == str(cid)


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
        uuid4(),
        uuid4(),
        aid,
        AssetTagBindingCreate(binding_value="X1", binding_kind="tid"),
    )
    assert out.binding_value == "X1"
    assert audit.entries[-1]["action"] == "asset.bound"
    assert audit.entries[-1]["changes"] == {"binding_value": "X1", "binding_kind": "tid"}


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


# ---- Sprint 37: list_assets forwards category_id to the repo ---------
#
# Promotes the Category filter from client-side (UI-only) to server-side
# (row 3.3a in docs/design/reference-design-remediation.md). The route
# layer accepts ``?category_id=<uuid>``; the service must pass it through
# to the repo unchanged so the SQL where clause can apply it.
#
# Sprint 42 generalises this to multi-category: the repo now takes
# ``category_ids: list[UUID] | None`` and the service collapses
# (legacy ``category_id``, new ``category_ids``) into a single dedup-ed
# list. These tests pin both that legacy callers (singular) still work
# and that the new plural kwarg flows through correctly.


@pytest.mark.asyncio
async def test_list_assets_forwards_legacy_category_id_to_repo() -> None:
    """Legacy singular kwarg should arrive at the repo as a single-element
    list (service collapses)."""
    svc, asset_repo, _, _ = _service()
    tenant = uuid4()
    cid = uuid4()
    await svc.list_assets(tenant, category_id=cid)
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["category_ids"] == [cid]


@pytest.mark.asyncio
async def test_list_assets_forwards_statuses_and_sort() -> None:
    """Sprint 76 — multi-status + server sort kwargs reach the repo."""
    svc, asset_repo, _, _ = _service()
    await svc.list_assets(uuid4(), statuses=["active", "in_transit"], sort="name", order="asc")
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["statuses"] == ["active", "in_transit"]
    assert asset_repo.last_list_kwargs["sort"] == "name"
    assert asset_repo.last_list_kwargs["order"] == "asc"
    # Other filters left at their defaults — kwarg threading is additive.
    assert asset_repo.last_list_kwargs["status"] is None


@pytest.mark.asyncio
async def test_list_assets_forwards_category_ids_to_repo() -> None:
    """Sprint 42: plural kwarg flows through verbatim (preserving order
    and deduplicating)."""
    svc, asset_repo, _, _ = _service()
    tenant = uuid4()
    cids = [uuid4(), uuid4()]
    await svc.list_assets(tenant, category_ids=cids)
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["category_ids"] == cids


@pytest.mark.asyncio
async def test_list_assets_merges_legacy_and_plural_uniquely() -> None:
    """When both kwargs are supplied the service unions them and dedupes
    so a client that accidentally sends the same id twice doesn't blow up
    the ``IN`` list."""
    svc, asset_repo, _, _ = _service()
    a, b = uuid4(), uuid4()
    await svc.list_assets(uuid4(), category_id=a, category_ids=[a, b])
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["category_ids"] == [a, b]


@pytest.mark.asyncio
async def test_list_assets_category_filter_defaults_to_none() -> None:
    """No kwargs ⇒ ``category_ids=None`` so the repo skips the predicate."""
    svc, asset_repo, _, _ = _service()
    await svc.list_assets(uuid4())
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["category_ids"] is None


@pytest.mark.asyncio
async def test_list_assets_empty_category_ids_treated_as_none() -> None:
    """An explicit empty list should not produce ``IN ()`` (invalid SQL)
    — the service maps it to ``None`` and the repo skips the predicate."""
    svc, asset_repo, _, _ = _service()
    await svc.list_assets(uuid4(), category_ids=[])
    assert asset_repo.last_list_kwargs is not None
    assert asset_repo.last_list_kwargs["category_ids"] is None


# ---- Audit mitigation tests (Phase A-C) ------------------------------


class _KeyedAssetRepo:
    """Asset repo keyed by id, with a working set_parent + get."""

    def __init__(self) -> None:
        self.assets: dict[UUID, AssetResponse] = {}

    def add(self, asset: AssetResponse) -> None:
        self.assets[asset.id] = asset

    async def get(  # type: ignore[no-untyped-def]
        self, tenant_id: UUID, asset_id: UUID
    ) -> AssetResponse | None:
        return self.assets.get(asset_id)

    async def set_parent(  # type: ignore[no-untyped-def]
        self, tenant_id, asset_id, parent_asset_id
    ):
        row = self.assets.get(asset_id)
        if row is None:
            return None
        prior = row.parent_asset_id
        updated = row.model_copy(update={"parent_asset_id": parent_asset_id})
        self.assets[asset_id] = updated
        return updated, prior


@pytest.mark.asyncio
async def test_load_onto_carrier_blocks_direct_self_loop() -> None:
    svc, asset_repo, _, _ = _service()
    aid = uuid4()
    asset_repo.next_response = _asset(uuid4(), id=aid)
    with pytest.raises(ValueError, match="own parent"):
        await svc.load_onto_carrier(uuid4(), uuid4(), aid, aid)


@pytest.mark.asyncio
async def test_load_onto_carrier_blocks_multi_step_cycle() -> None:
    """A→B→A cycle must be refused before set_parent runs."""
    tenant = uuid4()
    a_id, b_id = uuid4(), uuid4()
    keyed = _KeyedAssetRepo()
    # Existing: B is already a child of A. Attempt: load A onto B → cycle.
    keyed.add(_asset(tenant, id=a_id, name="A"))
    keyed.add(_asset(tenant, id=b_id, name="B").model_copy(update={"parent_asset_id": a_id}))
    audit = _FakeAudit()
    svc = AssetService(
        asset_repo=keyed,  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="containment cycle"):
        await svc.load_onto_carrier(tenant, uuid4(), a_id, b_id)
    # No mutation should have happened.
    assert keyed.assets[a_id].parent_asset_id is None
    assert audit.entries == []


@pytest.mark.asyncio
async def test_load_onto_carrier_allows_normal_attach() -> None:
    tenant = uuid4()
    child_id, parent_id = uuid4(), uuid4()
    keyed = _KeyedAssetRepo()
    keyed.add(_asset(tenant, id=child_id, name="child"))
    keyed.add(_asset(tenant, id=parent_id, name="parent"))
    svc = AssetService(
        asset_repo=keyed,  # type: ignore[arg-type]
        binding_repo=_FakeBindingRepo(),  # type: ignore[arg-type]
        audit=_FakeAudit(),  # type: ignore[arg-type]
    )
    out = await svc.load_onto_carrier(tenant, uuid4(), child_id, parent_id)
    assert out.parent_asset_id == parent_id


def test_zone_create_validator_blocks_empty_readers() -> None:
    from pydantic import ValidationError

    from tagpulse.models.schemas import ZoneCreate

    with pytest.raises(ValidationError):
        ZoneCreate(site_id=uuid4(), name="Z", fixed_reader_ids=[])


def test_zone_update_validator_blocks_empty_readers() -> None:
    from pydantic import ValidationError

    from tagpulse.models.schemas import ZoneUpdate

    # None is fine (not provided), [] is rejected.
    ZoneUpdate()  # no error
    with pytest.raises(ValidationError):
        ZoneUpdate(fixed_reader_ids=[])
