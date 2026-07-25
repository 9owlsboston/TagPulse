"""TimescaleDB implementation of external_locations repository.

Sprint 15 Phase C — see docs/design/mobile-carriers-and-manifests.md §10 Q5.
Sprint 80 (I-9HQA) — generalized from asset-only to any ``(subject_kind,
subject_id)``; the ``*_for_asset`` methods delegate to the generic ones. See
docs/design/external-locations-subject-kinds.md.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import ExternalLocationModel
from tagpulse.models.schemas import (
    ExternalLocationCreate,
    ExternalLocationResponse,
)


def _to_response(row: ExternalLocationModel) -> ExternalLocationResponse:
    return ExternalLocationResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        asset_id=row.asset_id,
        subject_kind=row.subject_kind,
        subject_id=row.subject_id,
        recorded_at=row.recorded_at,
        latitude=row.latitude,
        longitude=row.longitude,
        source=row.source,
        accuracy_meters=row.accuracy_meters,
        speed_kph=row.speed_kph,
        heading_deg=row.heading_deg,
        metadata=row.metadata_,
    )


class TimescaleExternalLocationRepository:
    """Persists external_locations rows and supports latest-position lookup.

    Generic ``*_for_subject`` methods work for any ``subject_kind``; the
    ``*_for_asset`` methods are the backward-compatible asset shims (they set
    ``subject_kind='asset'`` and keep ``asset_id`` populated so existing asset
    queries + ``ix_external_locations_by_asset`` are unaffected).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_for_subject(
        self,
        tenant_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        position: ExternalLocationCreate,
        *,
        asset_id: uuid.UUID | None = None,
    ) -> ExternalLocationResponse:
        row = ExternalLocationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            asset_id=asset_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            recorded_at=position.recorded_at,
            latitude=position.latitude,
            longitude=position.longitude,
            source=position.source,
            accuracy_meters=position.accuracy_meters,
            speed_kph=position.speed_kph,
            heading_deg=position.heading_deg,
            metadata_=position.metadata,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_response(row)

    async def insert(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        position: ExternalLocationCreate,
    ) -> ExternalLocationResponse:
        """Asset shim — records an asset position (``subject_kind='asset'``)."""
        return await self.insert_for_subject(
            tenant_id, "asset", asset_id, position, asset_id=asset_id
        )

    async def get_latest_for_subject(
        self, tenant_id: uuid.UUID, subject_kind: str, subject_id: uuid.UUID
    ) -> ExternalLocationResponse | None:
        stmt = (
            select(ExternalLocationModel)
            .where(
                ExternalLocationModel.tenant_id == tenant_id,
                ExternalLocationModel.subject_kind == subject_kind,
                ExternalLocationModel.subject_id == subject_id,
            )
            .order_by(ExternalLocationModel.recorded_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def get_latest_for_asset(
        self, tenant_id: uuid.UUID, asset_id: uuid.UUID
    ) -> ExternalLocationResponse | None:
        # Filter on ``asset_id`` (not the subject columns): asset rows always
        # populate it, including any written by pre-060 code during the expand
        # window whose ``subject_id`` is still NULL. Backward-compatible with
        # the original asset-only query.
        stmt = (
            select(ExternalLocationModel)
            .where(
                ExternalLocationModel.tenant_id == tenant_id,
                ExternalLocationModel.asset_id == asset_id,
            )
            .order_by(ExternalLocationModel.recorded_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def list_for_subject(
        self,
        tenant_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExternalLocationResponse]:
        stmt = (
            select(ExternalLocationModel)
            .where(
                ExternalLocationModel.tenant_id == tenant_id,
                ExternalLocationModel.subject_kind == subject_kind,
                ExternalLocationModel.subject_id == subject_id,
            )
            .order_by(ExternalLocationModel.recorded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]

    async def list_for_asset(
        self,
        tenant_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExternalLocationResponse]:
        # See :meth:`get_latest_for_asset` — filter on ``asset_id`` so expand-
        # window rows with a NULL ``subject_id`` remain visible.
        stmt = (
            select(ExternalLocationModel)
            .where(
                ExternalLocationModel.tenant_id == tenant_id,
                ExternalLocationModel.asset_id == asset_id,
            )
            .order_by(ExternalLocationModel.recorded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]
