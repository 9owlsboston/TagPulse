"""Rules and alerts service — CRUD, evaluation engine, and alert creation."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AlertModel, RuleModel
from tagpulse.models.rule_schemas import AlertResponse, RuleCreate, RuleResponse, RuleUpdate

logger = logging.getLogger(__name__)


class RulesService:
    """Manages rules CRUD, evaluation, and alert creation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- CRUD --

    async def create_rule(
        self, tenant_id: uuid.UUID, rule: RuleCreate
    ) -> RuleResponse:
        row = RuleModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=rule.name,
            description=rule.description,
            condition_type=rule.condition_type,
            condition_config=rule.condition_config,
            action_type=rule.action_type,
            action_config=rule.action_config,
            scope_device_id=rule.scope_device_id,
            enabled=rule.enabled,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("Rule created: id=%s name=%s tenant=%s", row.id, row.name, tenant_id)
        return _rule_to_response(row)

    async def get_rule(
        self, tenant_id: uuid.UUID, rule_id: uuid.UUID
    ) -> RuleResponse | None:
        stmt = select(RuleModel).where(
            RuleModel.id == rule_id, RuleModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _rule_to_response(row) if row else None

    async def list_rules(
        self,
        tenant_id: uuid.UUID,
        *,
        enabled_only: bool = False,
    ) -> list[RuleResponse]:
        stmt = select(RuleModel).where(
            RuleModel.tenant_id == tenant_id
        ).order_by(RuleModel.created_at.desc())
        if enabled_only:
            stmt = stmt.where(RuleModel.enabled.is_(True))
        result = await self._session.execute(stmt)
        return [_rule_to_response(row) for row in result.scalars()]

    async def update_rule(
        self, tenant_id: uuid.UUID, rule_id: uuid.UUID, patch: RuleUpdate
    ) -> RuleResponse | None:
        stmt = select(RuleModel).where(
            RuleModel.id == rule_id, RuleModel.tenant_id == tenant_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        for key, value in patch.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self._session.flush()
        logger.info("Rule updated: id=%s", rule_id)
        return _rule_to_response(row)

    async def delete_rule(
        self, tenant_id: uuid.UUID, rule_id: uuid.UUID
    ) -> bool:
        stmt = (
            delete(RuleModel)
            .where(RuleModel.id == rule_id, RuleModel.tenant_id == tenant_id)
            .returning(RuleModel.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.scalar_one_or_none()
        if deleted:
            logger.info("Rule deleted: id=%s", rule_id)
        return deleted is not None

    # -- Active rules for evaluation --

    async def get_active_rules_for_device(
        self, tenant_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[RuleResponse]:
        """Get enabled rules scoped to a specific device or global."""
        stmt = select(RuleModel).where(
            RuleModel.tenant_id == tenant_id,
            RuleModel.enabled.is_(True),
            (RuleModel.scope_device_id == device_id)
            | (RuleModel.scope_device_id.is_(None)),
        )
        result = await self._session.execute(stmt)
        return [_rule_to_response(row) for row in result.scalars()]

    async def get_active_rules_by_condition_type(
        self, tenant_id: uuid.UUID, condition_type: str
    ) -> list[RuleResponse]:
        """Get enabled rules of a specific condition_type for a tenant.

        Used by the inventory rule worker (Phase E) to evaluate
        ``stock.below_threshold`` / ``stock.expiring_within`` rules and by
        the zone-changed evaluator branch for ``stock.unexpected_in_zone``.
        """
        stmt = select(RuleModel).where(
            RuleModel.tenant_id == tenant_id,
            RuleModel.enabled.is_(True),
            RuleModel.condition_type == condition_type,
        )
        result = await self._session.execute(stmt)
        return [_rule_to_response(row) for row in result.scalars()]

    async def get_active_rules_by_condition_types_all_tenants(
        self, condition_types: list[str]
    ) -> list[RuleResponse]:
        """Cross-tenant scan used by background workers.

        Returns enabled rules across all tenants whose ``condition_type`` is
        in ``condition_types``. The worker handles per-tenant fan-out and
        always passes the rule's own ``tenant_id`` when creating alerts —
        no tenant data crosses boundaries.
        """
        if not condition_types:
            return []
        stmt = select(RuleModel).where(
            RuleModel.enabled.is_(True),
            RuleModel.condition_type.in_(condition_types),
        )
        result = await self._session.execute(stmt)
        return [_rule_to_response(row) for row in result.scalars()]

    # -- Alerts --

    async def create_alert(
        self,
        tenant_id: uuid.UUID,
        rule_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None,
        severity: str,
        message: str,
        context: dict[str, Any],
    ) -> AlertResponse:
        row = AlertModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            rule_id=rule_id,
            device_id=device_id,
            severity=severity,
            message=message,
            context=context,
            status="open",
            triggered_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "Alert created: id=%s rule=%s device=%s",
            row.id, rule_id, device_id,
        )
        return _alert_to_response(row)

    async def list_alerts(
        self,
        tenant_id: uuid.UUID,
        *,
        rule_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlertResponse]:
        stmt = select(AlertModel).where(
            AlertModel.tenant_id == tenant_id
        ).order_by(AlertModel.triggered_at.desc())
        if rule_id is not None:
            stmt = stmt.where(AlertModel.rule_id == rule_id)
        if device_id is not None:
            stmt = stmt.where(AlertModel.device_id == device_id)
        if status is not None:
            stmt = stmt.where(AlertModel.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_alert_to_response(row) for row in result.scalars()]

    async def acknowledge_alert(
        self, tenant_id: uuid.UUID, alert_id: uuid.UUID
    ) -> bool:
        stmt = (
            update(AlertModel)
            .where(
                AlertModel.id == alert_id,
                AlertModel.tenant_id == tenant_id,
            )
            .values(status="acknowledged")
            .returning(AlertModel.id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None


def _rule_to_response(row: RuleModel) -> RuleResponse:
    return RuleResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description,
        condition_type=row.condition_type,
        condition_config=row.condition_config,
        action_type=row.action_type,
        action_config=row.action_config,
        scope_device_id=row.scope_device_id,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _alert_to_response(row: AlertModel) -> AlertResponse:
    return AlertResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        rule_id=row.rule_id,
        device_id=row.device_id,
        severity=row.severity,
        message=row.message,
        context=row.context,
        status=row.status,
        triggered_at=row.triggered_at,
    )
