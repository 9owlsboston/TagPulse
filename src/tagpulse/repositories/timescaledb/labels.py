"""TimescaleDB repository for Labels.

Sprint 35; implements [ADR-020](../../../../docs/adr/020-labels-first-class.md).

Two related concerns live here:

- **Catalog** — :class:`LabelModel` rows. Tenant-scoped, unique by
  ``(tenant_id, entity_type, lower(key))`` (functional index, see
  migration 039). Methods: ``list_for_tenant``, ``get``,
  ``find_by_key``, ``create``, ``update``, ``count_associations``,
  ``delete``.
- **Association** — :class:`EntityLabelModel` rows. Polymorphic
  ``entity_id`` (no FK), composite PK ``(label_id, entity_id)``,
  30-per-entity cap enforced by the ``trg_enforce_label_cap``
  trigger. Methods: ``list_for_entity``, ``associate``,
  ``disassociate``.

The split repository / route pattern mirrors
``tagpulse.repositories.timescaledb.categories`` — domain
exceptions are raised here and converted to HTTPException in the
route layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import EntityLabelModel, LabelModel
from tagpulse.models.schemas import (
    LabelAssociationResponse,
    LabelCreate,
    LabelResponse,
    LabelUpdate,
)


def _to_response(row: LabelModel) -> LabelResponse:
    return LabelResponse.model_validate(row)


def _pg_sqlstate(exc: IntegrityError) -> str | None:
    """Pull the 5-char SQLSTATE out of an asyncpg/psycopg2 error.

    asyncpg exposes ``.sqlstate``; psycopg2 exposes ``.pgcode``. We
    accept either so the same error mapping works in both engines
    (asyncpg in prod, psycopg2 in some test contexts).
    """
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


class LabelKeyConflictError(ValueError):
    """Raised on case-insensitive key collision within (tenant, entity_type)."""


class LabelInUseError(RuntimeError):
    """Raised when DELETE is attempted on a label that still has associations."""

    def __init__(self, label_id: uuid.UUID, association_count: int) -> None:
        super().__init__(f"Label {label_id} still has {association_count} association(s)")
        self.label_id = label_id
        self.association_count = association_count


class LabelCapExceededError(RuntimeError):
    """Raised when associating would exceed the 30-per-entity cap.

    The DB trigger ``trg_enforce_label_cap`` raises SQLSTATE 23514
    on the 31st INSERT. We translate that into this domain
    exception so the route layer can return a structured 409 with
    the cap value the UI needs to render its guidance.
    """

    CAP = 30

    def __init__(self, entity_id: uuid.UUID) -> None:
        super().__init__(f"Entity {entity_id} already has the maximum {self.CAP} labels")
        self.entity_id = entity_id


class TimescaleLabelRepository:
    """Persists labels and label-to-entity associations to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Catalog operations
    # ------------------------------------------------------------------

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LabelResponse]:
        stmt = select(LabelModel).where(LabelModel.tenant_id == tenant_id)
        if entity_type is not None:
            stmt = stmt.where(LabelModel.entity_type == entity_type)
        stmt = (
            stmt.order_by(LabelModel.entity_type.asc(), LabelModel.key.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]

    async def get(self, tenant_id: uuid.UUID, label_id: uuid.UUID) -> LabelResponse | None:
        stmt = select(LabelModel).where(
            LabelModel.id == label_id,
            LabelModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def find_by_key(
        self,
        tenant_id: uuid.UUID,
        entity_type: str,
        key: str,
    ) -> LabelResponse | None:
        """Case-insensitive lookup by ``(tenant_id, entity_type, key)``.

        Mirrors the functional UNIQUE index in migration 039 so the
        association path (``POST /{entity_type}/{id}/labels``) can
        resolve a caller-supplied ``key`` to a catalog row.
        """
        stmt = select(LabelModel).where(
            LabelModel.tenant_id == tenant_id,
            LabelModel.entity_type == entity_type,
            func.lower(LabelModel.key) == key.lower(),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def create(
        self,
        tenant_id: uuid.UUID,
        payload: LabelCreate,
        *,
        user_id: uuid.UUID | None = None,
    ) -> LabelResponse:
        row = LabelModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity_type=payload.entity_type,
            key=payload.key,
            color=payload.color,
            created_by=user_id,
            updated_by=user_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # 23505 = unique_violation against the functional index.
            if _pg_sqlstate(exc) == "23505":
                raise LabelKeyConflictError(
                    f"Label '{payload.key}' already exists for "
                    f"entity_type '{payload.entity_type}' in this tenant"
                ) from exc
            raise
        return _to_response(row)

    async def update(
        self,
        tenant_id: uuid.UUID,
        label_id: uuid.UUID,
        patch: LabelUpdate,
        *,
        user_id: uuid.UUID | None = None,
    ) -> LabelResponse | None:
        stmt = select(LabelModel).where(
            LabelModel.id == label_id,
            LabelModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        patch_data = patch.model_dump(exclude_unset=True)
        for k, v in patch_data.items():
            setattr(row, k, v)
        if patch_data:
            row.updated_by = user_id
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if _pg_sqlstate(exc) == "23505":
                raise LabelKeyConflictError(
                    f"Label '{patch.key}' already exists for "
                    f"entity_type '{row.entity_type}' in this tenant"
                ) from exc
            raise
        return _to_response(row)

    async def count_associations(self, tenant_id: uuid.UUID, label_id: uuid.UUID) -> int:
        """Count rows in ``entity_labels`` for a given label.

        The join through ``labels`` keeps the count tenant-scoped —
        even though the FK already implies it, doing the explicit
        ``LabelModel.tenant_id`` predicate matches the project-wide
        "every query filters by tenant" convention.
        """
        stmt = (
            select(func.count())
            .select_from(EntityLabelModel)
            .join(LabelModel, EntityLabelModel.label_id == LabelModel.id)
            .where(
                LabelModel.tenant_id == tenant_id,
                EntityLabelModel.label_id == label_id,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete(self, tenant_id: uuid.UUID, label_id: uuid.UUID) -> bool:
        """Hard delete. Raises :class:`LabelInUseError` if any association exists.

        Returns ``False`` if the label does not exist in this tenant.
        """
        in_use = await self.count_associations(tenant_id, label_id)
        if in_use > 0:
            raise LabelInUseError(label_id, in_use)
        stmt = select(LabelModel).where(
            LabelModel.id == label_id,
            LabelModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Association operations
    # ------------------------------------------------------------------

    async def list_for_entity(
        self,
        tenant_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> list[LabelAssociationResponse]:
        """Return all label-value pairs attached to a single entity.

        Joins ``entity_labels`` with its parent ``labels`` so the
        response carries ``key`` / ``color`` along with the polymorphic
        ``entity_id``. The ``LabelModel.entity_type`` predicate is
        belt-and-braces: the cap trigger doesn't enforce
        entity_type alignment, so a stale row from a renamed label
        would simply not appear here.
        """
        stmt = (
            select(
                EntityLabelModel.label_id,
                EntityLabelModel.entity_id,
                EntityLabelModel.value,
                EntityLabelModel.created_by,
                EntityLabelModel.created_at,
                LabelModel.entity_type,
                LabelModel.key,
                LabelModel.color,
            )
            .select_from(EntityLabelModel)
            .join(LabelModel, EntityLabelModel.label_id == LabelModel.id)
            .where(
                LabelModel.tenant_id == tenant_id,
                LabelModel.entity_type == entity_type,
                EntityLabelModel.entity_id == entity_id,
            )
            .order_by(LabelModel.key.asc())
        )
        result = await self._session.execute(stmt)
        return [
            LabelAssociationResponse(
                label_id=r.label_id,
                entity_id=r.entity_id,
                entity_type=r.entity_type,
                key=r.key,
                value=r.value,
                color=r.color,
                created_by=r.created_by,
                created_at=r.created_at,
            )
            for r in result
        ]

    async def get_association(
        self,
        tenant_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        label_id: uuid.UUID,
    ) -> LabelAssociationResponse | None:
        """Fetch one association — used to build audit ``changes`` and
        to detect "already associated" on POST."""
        stmt = (
            select(
                EntityLabelModel.label_id,
                EntityLabelModel.entity_id,
                EntityLabelModel.value,
                EntityLabelModel.created_by,
                EntityLabelModel.created_at,
                LabelModel.entity_type,
                LabelModel.key,
                LabelModel.color,
            )
            .select_from(EntityLabelModel)
            .join(LabelModel, EntityLabelModel.label_id == LabelModel.id)
            .where(
                LabelModel.tenant_id == tenant_id,
                LabelModel.entity_type == entity_type,
                EntityLabelModel.entity_id == entity_id,
                EntityLabelModel.label_id == label_id,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return LabelAssociationResponse(
            label_id=row.label_id,
            entity_id=row.entity_id,
            entity_type=row.entity_type,
            key=row.key,
            value=row.value,
            color=row.color,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def associate(
        self,
        tenant_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        *,
        label: LabelResponse,
        value: str,
        user_id: uuid.UUID | None = None,
    ) -> LabelAssociationResponse:
        """Create an ``entity_labels`` row.

        Translates SQLSTATE 23514 (raised by the
        ``trg_enforce_label_cap`` trigger on the 31st INSERT) to
        :class:`LabelCapExceededError`. Translates SQLSTATE 23505
        (composite PK collision — same label already associated to
        the same entity) to :class:`LabelKeyConflictError` so the
        route can return 409.
        """
        if label.entity_type != entity_type:
            # Shouldn't happen — the route looks up the label by the
            # URL's entity_type. Defensive: surfaces the bug rather
            # than silently associating a mismatched label.
            raise ValueError(
                f"label.entity_type={label.entity_type!r} does not match "
                f"URL entity_type={entity_type!r}"
            )
        row = EntityLabelModel(
            label_id=label.id,
            entity_id=entity_id,
            value=value,
            created_by=user_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            code = _pg_sqlstate(exc)
            if code == "23514":
                # The cap trigger is the only 23514 we should hit
                # here — Pydantic validates the value regex before
                # the flush.
                raise LabelCapExceededError(entity_id) from exc
            if code == "23505":
                raise LabelKeyConflictError(
                    f"Label '{label.key}' is already associated to entity {entity_id}"
                ) from exc
            raise
        return LabelAssociationResponse(
            label_id=label.id,
            entity_id=entity_id,
            entity_type=label.entity_type,
            key=label.key,
            value=value,
            color=label.color,
            created_by=user_id,
            created_at=row.created_at,
        )

    async def disassociate(
        self,
        tenant_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        label_id: uuid.UUID,
    ) -> bool:
        """Delete one ``entity_labels`` row. Returns ``False`` if no
        such association exists (route returns 404)."""
        # Tenant-scoped lookup via the parent label row; never delete
        # by label_id alone or a malicious caller could remove
        # associations belonging to another tenant by guessing UUIDs.
        stmt = (
            select(EntityLabelModel)
            .join(LabelModel, EntityLabelModel.label_id == LabelModel.id)
            .where(
                LabelModel.tenant_id == tenant_id,
                LabelModel.entity_type == entity_type,
                EntityLabelModel.entity_id == entity_id,
                EntityLabelModel.label_id == label_id,
            )
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
