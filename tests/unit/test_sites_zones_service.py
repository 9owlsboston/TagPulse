"""Unit tests for the SiteZoneService (Sprint 15)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tagpulse.api.services.sites_zones_service import SiteZoneService
from tagpulse.models.schemas import (
    SiteCreate,
    SiteResponse,
    SiteUpdate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)


def _site(tenant_id: UUID, **overrides: Any) -> SiteResponse:
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Site A",
        address=None,
        default_timezone="UTC",
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return SiteResponse(**base)


def _zone(tenant_id: UUID, site_id: UUID, **overrides: Any) -> ZoneResponse:
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        site_id=site_id,
        name="Dock-1",
        kind="reader_bound",
        fixed_reader_ids=[uuid4()],
        polygon_geojson=None,
        metadata=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ZoneResponse(**base)


class _FakeSiteRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.next_response: Any = None

    async def create(self, tenant_id: UUID, payload: SiteCreate) -> SiteResponse:
        self.calls.append(("create", payload))
        return self.next_response or _site(tenant_id, name=payload.name)

    async def get(self, tenant_id: UUID, site_id: UUID) -> SiteResponse | None:
        self.calls.append(("get", site_id))
        return self.next_response

    async def list(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> list[SiteResponse]:
        self.calls.append(("list", (limit, offset)))
        return self.next_response or []

    async def update(
        self, tenant_id: UUID, site_id: UUID, patch: SiteUpdate
    ) -> SiteResponse | None:
        self.calls.append(("update", (site_id, patch)))
        return self.next_response

    async def delete(self, tenant_id: UUID, site_id: UUID) -> bool:
        self.calls.append(("delete", site_id))
        return bool(self.next_response)


class _FakeZoneRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.next_response: Any = None

    async def create(self, tenant_id: UUID, payload: ZoneCreate) -> ZoneResponse:
        self.calls.append(("create", payload))
        return self.next_response or _zone(
            tenant_id, payload.site_id, name=payload.name
        )

    async def get(self, tenant_id: UUID, zone_id: UUID) -> ZoneResponse | None:
        self.calls.append(("get", zone_id))
        return self.next_response

    async def list(
        self,
        tenant_id: UUID,
        *,
        site_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[ZoneResponse]:
        self.calls.append(("list", (site_id, limit, offset)))
        return self.next_response or []

    async def update(
        self, tenant_id: UUID, zone_id: UUID, patch: ZoneUpdate
    ) -> ZoneResponse | None:
        self.calls.append(("update", (zone_id, patch)))
        return self.next_response

    async def delete(self, tenant_id: UUID, zone_id: UUID) -> bool:
        self.calls.append(("delete", zone_id))
        return bool(self.next_response)

    async def get_zone_for_reader(
        self, tenant_id: UUID, device_id: UUID
    ) -> ZoneResponse | None:
        self.calls.append(("get_zone_for_reader", device_id))
        return self.next_response


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(
        self,
        tenant_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        changes: dict[str, Any] | None = None,
        *,
        user_id: UUID | None = None,
    ) -> None:
        self.entries.append(
            {
                "tenant_id": tenant_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "changes": changes,
                "user_id": user_id,
            }
        )


def _service() -> tuple[SiteZoneService, _FakeSiteRepo, _FakeZoneRepo, _FakeAudit]:
    site_repo = _FakeSiteRepo()
    zone_repo = _FakeZoneRepo()
    audit = _FakeAudit()
    svc = SiteZoneService(
        site_repo=site_repo,  # type: ignore[arg-type]
        zone_repo=zone_repo,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
    )
    return svc, site_repo, zone_repo, audit


@pytest.mark.asyncio
async def test_create_site_writes_audit_entry() -> None:
    svc, _, _, audit = _service()
    tenant = uuid4()
    user = uuid4()

    site = await svc.create_site(
        tenant, user, SiteCreate(name="HQ", default_timezone="America/New_York")
    )

    assert site.name == "HQ"
    assert len(audit.entries) == 1
    assert audit.entries[0]["action"] == "site.created"
    assert audit.entries[0]["user_id"] == user
    assert audit.entries[0]["resource_type"] == "site"


@pytest.mark.asyncio
async def test_update_site_returns_none_when_missing() -> None:
    svc, site_repo, _, audit = _service()
    site_repo.next_response = None

    result = await svc.update_site(
        uuid4(), uuid4(), uuid4(), SiteUpdate(name="renamed")
    )

    assert result is None
    assert audit.entries == []


@pytest.mark.asyncio
async def test_delete_site_audits_only_when_deleted() -> None:
    svc, site_repo, _, audit = _service()
    site_repo.next_response = True

    deleted = await svc.delete_site(uuid4(), uuid4(), uuid4())

    assert deleted is True
    assert len(audit.entries) == 1
    assert audit.entries[0]["action"] == "site.deleted"


@pytest.mark.asyncio
async def test_create_zone_audits_with_site_id_in_changes() -> None:
    svc, _, _, audit = _service()
    tenant = uuid4()
    site = uuid4()
    reader = uuid4()

    zone = await svc.create_zone(
        tenant,
        uuid4(),
        ZoneCreate(site_id=site, name="Bay-1", fixed_reader_ids=[reader]),
    )

    assert zone.site_id == site
    assert audit.entries[-1]["action"] == "zone.created"
    assert audit.entries[-1]["changes"]["site_id"] == str(site)


@pytest.mark.asyncio
async def test_get_zone_for_reader_delegates() -> None:
    svc, _, zone_repo, _ = _service()
    tenant = uuid4()
    reader = uuid4()
    site = uuid4()
    expected = _zone(tenant, site)
    zone_repo.next_response = expected

    result = await svc.get_zone_for_reader(tenant, reader)

    assert result is expected
    assert zone_repo.calls[-1] == ("get_zone_for_reader", reader)


# -- Schema-level guard for zone kind/payload mismatch --

from uuid import uuid4 as _uuid4  # noqa: E402


@pytest.mark.asyncio
async def test_create_zone_rejects_reader_bound_without_readers() -> None:
    """Schema-level validator (ZoneCreate.model_validator) blocks the body
    before it ever reaches the route handler — see Phase A-C audit #7."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        ZoneCreate(site_id=_uuid4(), name="Bad", fixed_reader_ids=None)
    assert "fixed_reader_ids" in str(exc.value)


@pytest.mark.asyncio
async def test_create_zone_rejects_unknown_kind() -> None:
    """Pydantic Literal["reader_bound","geofence"] rejects unknown values at construction."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ZoneCreate(
            site_id=_uuid4(),
            name="Bad",
            kind="quantum",  # type: ignore[arg-type]
            fixed_reader_ids=[_uuid4()],
        )
