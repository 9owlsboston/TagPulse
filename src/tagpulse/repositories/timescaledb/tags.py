"""TimescaleDB repositories for the tag registry and tag transfers.

Sprint 50 — implements [ADR-028](../../../../docs/adr/028-tag-registry.md).

Two related repositories live here:

- :class:`TimescaleTagRepository` — per-tenant CRUD over ``tags``.
  Phase B exposes the operator surface: list (with status /
  epc_prefix / bound / label filters), get-by-id, get-by-epc,
  create (auto-derives ``gs1_uri`` via
  :func:`tagpulse.services.tags.parse_gs1_uri`), patch (status +
  metadata only, transitions validated by
  :func:`tagpulse.services.tags.validate_status_transition`),
  and hard-delete (gated by an "in use" check the route layer
  applies before calling — Phase B treats any
  ``stock_items.binding_value = epc_hex`` row as "in use").
- :class:`TimescaleTagTransferRepository` — append-only audit log
  for cross-tenant transfers. Phase B writes rows in
  ``status='requested'`` only; the acknowledgement / completion
  path lands in a later phase.

Domain exceptions are raised here and converted to ``HTTPException``
in the route layer. This file deliberately mirrors the structure of
:mod:`tagpulse.repositories.timescaledb.labels`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import (
    EntityLabelModel,
    LabelModel,
    StockItemModel,
    TagModel,
    TagTransferModel,
)
from tagpulse.models.schemas import (
    TagCreate,
    TagResponse,
    TagTransferResponse,
    TagUpdate,
)
from tagpulse.services.tags import (
    StatusTransitionError,
    parse_gs1_uri,
    validate_status_transition,
)


def _to_response(row: TagModel) -> TagResponse:
    return TagResponse.model_validate(row)


def _transfer_to_response(row: TagTransferModel) -> TagTransferResponse:
    return TagTransferResponse.model_validate(row)


def _pg_sqlstate(exc: IntegrityError) -> str | None:
    """Pull the 5-char SQLSTATE out of an asyncpg/psycopg2 error.

    Lifted verbatim from :mod:`tagpulse.repositories.timescaledb.labels` —
    asyncpg exposes ``.sqlstate``, psycopg2 exposes ``.pgcode``.
    """
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


class TagEpcConflictError(ValueError):
    """Raised when an EPC already exists for this tenant.

    Maps the 23505 SQLSTATE from ``uq_tags_tenant_epc`` to a 409
    in the route layer. The natural key is ``(tenant_id, epc_hex)``
    so the same EPC under two different tenants is *not* a
    conflict (and is, per ADR 028, a legitimate scenario when one
    physical tag is transferred between tenants).
    """


class TagInUseError(RuntimeError):
    """Raised when DELETE is attempted on a tag with bindings.

    Phase B definition of "in use" = at least one ``stock_items`` row
    binds to this tag's ``epc_hex``. The route layer issues a 409
    with the binding count.
    """

    def __init__(self, tag_id: uuid.UUID, binding_count: int) -> None:
        super().__init__(f"Tag {tag_id} still has {binding_count} stock binding(s)")
        self.tag_id = tag_id
        self.binding_count = binding_count


class TimescaleTagRepository:
    """Persists tag registry rows to TimescaleDB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        epc_prefix: str | None = None,
        bound: bool | None = None,
        label_filters: dict[str, str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagResponse]:
        """List tags with the Phase B filter surface.

        - ``status`` — exact match on the status enum.
        - ``epc_prefix`` — case-sensitive ``LIKE 'PREFIX%'`` on
          ``epc_hex``. The caller is expected to have already
          canonicalised the prefix (uppercase, no whitespace).
        - ``bound=True`` — only tags with at least one
          ``stock_items`` row whose ``binding_kind='epc'`` and
          ``binding_value=epc_hex``. ``bound=False`` — the inverse.
          ``None`` — no binding filter.
        - ``label_filters`` — ``{"batch": "B-001"}``-style mapping;
          AND-combined; each entry resolves to an
          ``entity_labels`` row with ``entity_type='tag'``,
          ``labels.key=<key>``, and matching ``value``. (Migration
          045 widened the label-entity CHECK to allow ``'tag'``.)
        """
        stmt = select(TagModel).where(TagModel.tenant_id == tenant_id)

        if status is not None:
            stmt = stmt.where(TagModel.status == status)
        if epc_prefix:
            stmt = stmt.where(TagModel.epc_hex.like(f"{epc_prefix}%"))

        if bound is not None:
            binding_exists = exists().where(
                and_(
                    StockItemModel.tenant_id == tenant_id,
                    StockItemModel.binding_kind == "epc",
                    StockItemModel.binding_value == TagModel.epc_hex,
                )
            )
            stmt = stmt.where(binding_exists if bound else ~binding_exists)

        for key, value in (label_filters or {}).items():
            label_match = exists().where(
                and_(
                    EntityLabelModel.entity_id == TagModel.id,
                    EntityLabelModel.value == value,
                    LabelModel.id == EntityLabelModel.label_id,
                    LabelModel.tenant_id == tenant_id,
                    LabelModel.entity_type == "tag",
                    func.lower(LabelModel.key) == key.lower(),
                )
            )
            stmt = stmt.where(label_match)

        stmt = stmt.order_by(TagModel.epc_hex.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_to_response(r) for r in result.scalars()]

    async def get(self, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> TagResponse | None:
        stmt = select(TagModel).where(
            TagModel.id == tag_id,
            TagModel.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def get_by_epc(self, tenant_id: uuid.UUID, epc_hex: str) -> TagResponse | None:
        stmt = select(TagModel).where(
            TagModel.tenant_id == tenant_id,
            TagModel.epc_hex == epc_hex,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_response(row) if row else None

    async def _get_row(self, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> TagModel | None:
        stmt = select(TagModel).where(
            TagModel.id == tag_id,
            TagModel.tenant_id == tenant_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        tenant_id: uuid.UUID,
        payload: TagCreate,
    ) -> TagResponse:
        """Insert a new tag in ``status='registered'``.

        ``gs1_uri`` is derived synchronously here — the parser is a
        pure-Python decode of the EPC header and a few-microsecond
        operation, so we don't bother deferring it to a worker. If
        the EPC doesn't decode cleanly, ``gs1_uri`` stays ``NULL``
        and the partial index skips it.
        """
        row = TagModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            epc_hex=payload.epc_hex,
            gs1_uri=parse_gs1_uri(payload.epc_hex),
            status="registered",
            source=payload.source,
            metadata_=payload.metadata,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if _pg_sqlstate(exc) == "23505":
                raise TagEpcConflictError(
                    f"EPC {payload.epc_hex!r} already exists in this tenant"
                ) from exc
            raise
        return _to_response(row)

    async def update(
        self,
        tenant_id: uuid.UUID,
        tag_id: uuid.UUID,
        patch: TagUpdate,
    ) -> TagResponse | None:
        """Patch ``status`` and/or ``metadata``.

        Raises :class:`StatusTransitionError` if the requested
        transition is not on the operator-permitted edge list (see
        :func:`tagpulse.services.tags.validate_status_transition`).
        Returns ``None`` if no such tag exists for the tenant.
        """
        row = await self._get_row(tenant_id, tag_id)
        if row is None:
            return None
        data = patch.model_dump(exclude_unset=True)
        if "status" in data and data["status"] is not None:
            validate_status_transition(row.status, data["status"])
            row.status = data["status"]
        if "metadata" in data:
            row.metadata_ = data["metadata"]
        await self._session.flush()
        return _to_response(row)

    async def update_status_to_transferred_out(
        self,
        tenant_id: uuid.UUID,
        epc_hex: str,
    ) -> TagResponse | None:
        """Privileged path used by the transfer flow.

        Bypasses :func:`validate_status_transition` because
        ``* → transferred_out`` is intentionally absent from the
        operator-permitted transition table. Caller must be inside
        the transfer-create transaction.
        """
        stmt = select(TagModel).where(
            TagModel.tenant_id == tenant_id,
            TagModel.epc_hex == epc_hex,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        row.status = "transferred_out"
        await self._session.flush()
        return _to_response(row)

    async def count_bindings(self, tenant_id: uuid.UUID, epc_hex: str) -> int:
        """Count ``stock_items`` rows referencing this EPC.

        Used by the route layer before calling :meth:`delete` to
        decide between 204 and 409.
        """
        stmt = (
            select(func.count())
            .select_from(StockItemModel)
            .where(
                StockItemModel.tenant_id == tenant_id,
                StockItemModel.binding_kind == "epc",
                StockItemModel.binding_value == epc_hex,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete(self, tenant_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        """Hard-delete a tag. Returns ``True`` if a row was removed.

        The caller is expected to have already checked
        :meth:`count_bindings` and raised :class:`TagInUseError`
        when appropriate — this method does *not* re-check (keeps
        the repository layer purely about persistence; the
        "in use" semantics live in the route).
        """
        row = await self._get_row(tenant_id, tag_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


class TimescaleTagTransferRepository:
    """Append-only repository for cross-tenant transfer audit rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_request(
        self,
        *,
        from_tenant_id: uuid.UUID,
        to_tenant_id: uuid.UUID,
        epcs: list[str],
        requested_by: uuid.UUID,
    ) -> list[TagTransferResponse]:
        """Write one row per EPC, all sharing one ``request_id``.

        All rows are written in ``status='requested'``. The
        acknowledgement / completion path is a separate write that
        lands in a later phase and flips them to ``completed`` (and
        sets the matching ``tags.status='transferred_out'``).
        """
        request_id = uuid.uuid4()
        rows = [
            TagTransferModel(
                id=uuid.uuid4(),
                request_id=request_id,
                from_tenant_id=from_tenant_id,
                to_tenant_id=to_tenant_id,
                epc_hex=epc,
                status="requested",
                requested_by=requested_by,
            )
            for epc in epcs
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return [_transfer_to_response(r) for r in rows]

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        direction: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TagTransferResponse]:
        """List transfers visible to ``tenant_id``.

        ``direction='out'`` filters to ``from_tenant_id == tenant_id``;
        ``'in'`` to ``to_tenant_id``; ``None`` returns both sides
        (matches the RLS policy ``tenant_isolation_tag_transfers``).
        """
        if direction == "out":
            stmt = select(TagTransferModel).where(TagTransferModel.from_tenant_id == tenant_id)
        elif direction == "in":
            stmt = select(TagTransferModel).where(TagTransferModel.to_tenant_id == tenant_id)
        else:
            stmt = select(TagTransferModel).where(
                (TagTransferModel.from_tenant_id == tenant_id)
                | (TagTransferModel.to_tenant_id == tenant_id)
            )
        if status is not None:
            stmt = stmt.where(TagTransferModel.status == status)
        stmt = stmt.order_by(TagTransferModel.requested_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_transfer_to_response(r) for r in result.scalars()]

    async def get(self, tenant_id: uuid.UUID, transfer_id: uuid.UUID) -> TagTransferResponse | None:
        stmt = select(TagTransferModel).where(
            TagTransferModel.id == transfer_id,
            (TagTransferModel.from_tenant_id == tenant_id)
            | (TagTransferModel.to_tenant_id == tenant_id),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _transfer_to_response(row) if row else None


__all__ = [
    "StatusTransitionError",
    "TagEpcConflictError",
    "TagInUseError",
    "TimescaleTagRepository",
    "TimescaleTagTransferRepository",
]
