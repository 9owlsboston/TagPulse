"""Integration service — CRUD for integration targets and delivery log."""

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import IntegrationDeliveryModel, IntegrationModel
from tagpulse.models.integration_schemas import (
    DeliveryResponse,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
)

logger = logging.getLogger(__name__)


class IntegrationService:
    """Manages integration target CRUD and delivery log queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: uuid.UUID, body: IntegrationCreate) -> IntegrationResponse:
        row = IntegrationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=body.name,
            type=body.type,
            events=body.events,
            config=body.config,
            filters=body.filters,
            enrichments=body.enrichments,
            enabled=body.enabled,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "Integration created: id=%s name=%s type=%s",
            row.id,
            row.name,
            row.type,
        )
        return _to_response(row)

    async def list_all(self, tenant_id: uuid.UUID) -> list[IntegrationResponse]:
        stmt = (
            select(IntegrationModel)
            .where(IntegrationModel.tenant_id == tenant_id)
            .order_by(IntegrationModel.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def get(
        self, tenant_id: uuid.UUID, integration_id: uuid.UUID
    ) -> IntegrationResponse | None:
        stmt = select(IntegrationModel).where(
            IntegrationModel.id == integration_id,
            IntegrationModel.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_response(row) if row else None

    async def update(
        self,
        tenant_id: uuid.UUID,
        integration_id: uuid.UUID,
        patch: IntegrationUpdate,
    ) -> IntegrationResponse | None:
        stmt = select(IntegrationModel).where(
            IntegrationModel.id == integration_id,
            IntegrationModel.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        for key, value in patch.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self._session.flush()
        logger.info("Integration updated: id=%s", integration_id)
        return _to_response(row)

    async def delete_integration(self, tenant_id: uuid.UUID, integration_id: uuid.UUID) -> bool:
        stmt = (
            delete(IntegrationModel)
            .where(
                IntegrationModel.id == integration_id,
                IntegrationModel.tenant_id == tenant_id,
            )
            .returning(IntegrationModel.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.scalar_one_or_none()
        if deleted:
            logger.info("Integration deleted: id=%s", integration_id)
        return deleted is not None

    async def get_enabled_for_event(
        self, tenant_id: uuid.UUID, event_type: str
    ) -> list[IntegrationResponse]:
        """Get enabled integrations subscribed to a specific event type."""
        stmt = select(IntegrationModel).where(
            IntegrationModel.tenant_id == tenant_id,
            IntegrationModel.enabled.is_(True),
            IntegrationModel.events.contains([event_type]),
        )
        result = await self._session.execute(stmt)
        return [_to_response(row) for row in result.scalars()]

    async def list_deliveries(
        self,
        tenant_id: uuid.UUID,
        integration_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeliveryResponse]:
        stmt = (
            select(IntegrationDeliveryModel)
            .where(
                IntegrationDeliveryModel.tenant_id == tenant_id,
                IntegrationDeliveryModel.integration_id == integration_id,
            )
            .order_by(IntegrationDeliveryModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [
            DeliveryResponse(
                id=row.id,
                integration_id=row.integration_id,
                event_type=row.event_type,
                status=row.status,
                attempts=row.attempts,
                response_code=row.response_code,
                error_message=row.error_message,
                created_at=row.created_at,
            )
            for row in result.scalars()
        ]


def _to_response(row: IntegrationModel) -> IntegrationResponse:
    return IntegrationResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        type=row.type,
        events=row.events,
        config=row.config,
        enabled=row.enabled,
        status=row.status,
        health_status=row.health_status,
        filters=row.filters,
        enrichments=row.enrichments,
        last_triggered=row.last_triggered,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
