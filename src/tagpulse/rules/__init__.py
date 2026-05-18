"""Rules and alerts service — CRUD, evaluation engine, and alert creation."""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import any_, delete, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tagpulse.models.database import AlertModel, RuleModel
from tagpulse.models.rule_schemas import (
    SIGNALING_DEFAULT_CAP_PER_SCOPE,
    AlertResponse,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
    validate_signaling_condition_config,
)

logger = logging.getLogger(__name__)


class SignalingScopeCapExceededError(Exception):
    """Raised when a signaling rule would exceed the per-scope active cap.

    Per ADR-021 v2 open question #4 / Sprint 41 Phase B2: at most
    :data:`tagpulse.models.rule_schemas.SIGNALING_DEFAULT_CAP_PER_SCOPE`
    active signaling rules per ``(tenant_id, event_type, category_id)``.
    Admin callers can bypass with ``?override=true`` which writes an
    audit-log entry instead of raising.
    """

    def __init__(
        self,
        *,
        tenant_id: uuid.UUID,
        event_type: str,
        category_id: uuid.UUID | None,
        current_count: int,
        cap: int,
    ) -> None:
        self.tenant_id = tenant_id
        self.event_type = event_type
        self.category_id = category_id
        self.current_count = current_count
        self.cap = cap
        scope = f"category_id={category_id}" if category_id is not None else "category_id=<unset>"
        super().__init__(
            f"Signaling rule cap exceeded: tenant={tenant_id} event_type={event_type!r} "
            f"{scope}: {current_count}/{cap} active rules already exist"
        )


class RulesService:
    """Manages rules CRUD, evaluation, and alert creation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- CRUD --

    async def create_rule(
        self,
        tenant_id: uuid.UUID,
        rule: RuleCreate,
        *,
        allow_cap_override: bool = False,
    ) -> RuleResponse:
        # Signaling-rule guard rails (ADR-021 v2 / Sprint 41 Phase B):
        # validate the trigger-specific config shape and enforce the
        # per-scope active cap. Legacy rules (NULL event_type) skip
        # both. ``allow_cap_override`` is set by the API layer when an
        # admin caller passes ``?override=true``; the audit-log entry is
        # written by the API after this method returns so the rule id
        # is known.
        validate_signaling_condition_config(rule.condition_type, rule.condition_config)
        if not allow_cap_override:
            await self._enforce_signaling_cap(
                tenant_id=tenant_id,
                event_type=rule.event_type,
                category_ids=list(rule.category_ids),
                enabled=rule.enabled,
            )
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
            event_type=rule.event_type,
            trigger=rule.trigger,
            processor=rule.processor,
            confidence_threshold=rule.confidence_threshold,
            category_ids=list(rule.category_ids),
            asset_label_filters=rule.asset_label_filters,
            zone_label_filters=rule.zone_label_filters,
            site_label_filters=rule.site_label_filters,
            integration_ids=rule.integration_ids,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("Rule created: id=%s name=%s tenant=%s", row.id, row.name, tenant_id)
        return _rule_to_response(row)

    async def get_rule(self, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> RuleResponse | None:
        stmt = select(RuleModel).where(RuleModel.id == rule_id, RuleModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _rule_to_response(row) if row else None

    async def list_rules(
        self,
        tenant_id: uuid.UUID,
        *,
        enabled_only: bool = False,
        kind: Literal["legacy", "signaling"] | None = None,
    ) -> list[RuleResponse]:
        """List rules for a tenant.

        ``kind='signaling'`` returns rows where ``event_type IS NOT NULL``
        (the implicit ADR-021 v2 discriminator). ``kind='legacy'`` returns
        rows where ``event_type IS NULL``. ``kind=None`` (default) returns
        all rules — backwards-compatible with pre-Sprint-41 callers.
        """

        stmt = (
            select(RuleModel)
            .where(RuleModel.tenant_id == tenant_id)
            .order_by(RuleModel.created_at.desc())
        )
        if enabled_only:
            stmt = stmt.where(RuleModel.enabled.is_(True))
        if kind == "signaling":
            stmt = stmt.where(RuleModel.event_type.is_not(None))
        elif kind == "legacy":
            stmt = stmt.where(RuleModel.event_type.is_(None))
        result = await self._session.execute(stmt)
        return [_rule_to_response(row) for row in result.scalars()]

    async def update_rule(
        self,
        tenant_id: uuid.UUID,
        rule_id: uuid.UUID,
        patch: RuleUpdate,
        *,
        allow_cap_override: bool = False,
    ) -> RuleResponse | None:
        stmt = select(RuleModel).where(RuleModel.id == rule_id, RuleModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        updates = patch.model_dump(exclude_unset=True)
        # Sprint 41 Phase B: when the patch toggles enabled True or
        # changes the scoping (event_type / category_ids), re-check the
        # cap with the *post-patch* shape. Skipped for legacy rules
        # (NULL event_type both before and after).
        new_event_type = updates.get("event_type", row.event_type)
        new_category_ids = updates.get("category_ids", list(row.category_ids or []))
        new_enabled = updates.get("enabled", row.enabled)
        scope_changed = (
            "event_type" in updates
            or "category_ids" in updates
            or ("enabled" in updates and updates["enabled"] is True and not row.enabled)
        )
        if scope_changed and new_event_type is not None and new_enabled and not allow_cap_override:
            await self._enforce_signaling_cap(
                tenant_id=tenant_id,
                event_type=new_event_type,
                category_ids=list(new_category_ids),
                enabled=True,
                exclude_rule_id=rule_id,
            )
        # If the patch changes condition_type or condition_config on a
        # signaling rule, re-validate the trigger config shape against
        # the new condition_type so a flip from periodic → on_change
        # cannot ride through with the old config.
        if "condition_type" in updates or "condition_config" in updates:
            new_condition_type = updates.get("condition_type", row.condition_type)
            new_condition_config = updates.get("condition_config", row.condition_config)
            validate_signaling_condition_config(new_condition_type, new_condition_config)
        for key, value in updates.items():
            setattr(row, key, value)
        await self._session.flush()
        logger.info("Rule updated: id=%s", rule_id)
        return _rule_to_response(row)

    async def delete_rule(self, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
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
            (RuleModel.scope_device_id == device_id) | (RuleModel.scope_device_id.is_(None)),
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

    # -- Signaling cap enforcement (Sprint 41 Phase B2) --

    async def count_active_signaling_rules_for_scope(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        category_id: uuid.UUID | None,
        *,
        exclude_rule_id: uuid.UUID | None = None,
    ) -> int:
        """Count active signaling rules in a single ``(event_type, category)`` scope.

        A rule contributes to the count for ``category_id=X`` when
        ``X`` is in its ``category_ids`` array. ``category_id=None``
        counts rules whose ``category_ids`` is empty (= broadcast to all
        categories of the matching event_type). ``exclude_rule_id``
        skips the row whose own update is being checked.
        """

        stmt = select(func.count(RuleModel.id)).where(
            RuleModel.tenant_id == tenant_id,
            RuleModel.enabled.is_(True),
            RuleModel.event_type == event_type,
        )
        if category_id is None:
            # Empty array via cardinality() is faster + clearer than = '{}'.
            stmt = stmt.where(func.cardinality(RuleModel.category_ids) == 0)
        else:
            # PG-idiomatic ``category_id = ANY(rule.category_ids)`` —
            # uses the GIN index on ``rules.category_ids`` (migration 040).
            stmt = stmt.where(literal(category_id) == any_(RuleModel.category_ids))
        if exclude_rule_id is not None:
            stmt = stmt.where(RuleModel.id != exclude_rule_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def _enforce_signaling_cap(
        self,
        *,
        tenant_id: uuid.UUID,
        event_type: str | None,
        category_ids: list[uuid.UUID],
        enabled: bool,
        exclude_rule_id: uuid.UUID | None = None,
        cap: int = SIGNALING_DEFAULT_CAP_PER_SCOPE,
    ) -> None:
        """Raise :class:`SignalingScopeCapExceededError` if any scope is full.

        Skipped silently for legacy rules (NULL event_type) and for
        disabled rules (they don't contribute to the active-rule
        budget). A rule with N categories occupies one slot in each of
        N scopes; one with no categories occupies the broadcast scope.
        """

        if event_type is None or not enabled:
            return
        scopes: list[uuid.UUID | None] = list(category_ids) if category_ids else [None]
        for cat_id in scopes:
            current = await self.count_active_signaling_rules_for_scope(
                tenant_id,
                event_type,
                cat_id,
                exclude_rule_id=exclude_rule_id,
            )
            # The about-to-be-created rule isn't in the count yet, so
            # ``current >= cap`` (not ``> cap``) is the violation.
            if current >= cap:
                raise SignalingScopeCapExceededError(
                    tenant_id=tenant_id,
                    event_type=event_type,
                    category_id=cat_id,
                    current_count=current,
                    cap=cap,
                )

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
            row.id,
            rule_id,
            device_id,
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
        stmt = (
            select(AlertModel)
            .where(AlertModel.tenant_id == tenant_id)
            .order_by(AlertModel.triggered_at.desc())
        )
        if rule_id is not None:
            stmt = stmt.where(AlertModel.rule_id == rule_id)
        if device_id is not None:
            stmt = stmt.where(AlertModel.device_id == device_id)
        if status is not None:
            stmt = stmt.where(AlertModel.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [_alert_to_response(row) for row in result.scalars()]

    async def acknowledge_alert(self, tenant_id: uuid.UUID, alert_id: uuid.UUID) -> bool:
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
        event_type=row.event_type,
        trigger=row.trigger,
        processor=row.processor,
        confidence_threshold=row.confidence_threshold or Decimal("0.0"),
        category_ids=list(row.category_ids or []),
        asset_label_filters=row.asset_label_filters,
        zone_label_filters=row.zone_label_filters,
        site_label_filters=row.site_label_filters,
        integration_ids=list(row.integration_ids) if row.integration_ids else None,
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
