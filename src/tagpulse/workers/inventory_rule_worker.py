"""Inventory rule worker (Sprint 15b Phase E).

Periodically scans inventory state and fires alerts for user-defined rules
of type ``stock.below_threshold`` and ``stock.expiring_within``. Also emits
the ``stock_items_active`` metering snapshot once per scan day.

Cadences (defaults):
- ``stock.below_threshold``: every 60 s
- ``stock.expiring_within``: once per day (configurable)

Both scans use a single asyncio loop; the daily scan runs only when the
calendar date in UTC has advanced since the last run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid as _uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.otel_metrics import (
    alerts_fired,
    rule_evaluations,
)
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.database import LotModel, StockItemModel
from tagpulse.repositories.timescaledb.inventory import (
    TimescaleStockItemRepository,
)
from tagpulse.rules import RulesService

logger = logging.getLogger(__name__)


class InventoryRuleWorker:
    """Periodic evaluator for inventory-condition rules."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        usage_meter: UsageMeter,
        *,
        below_threshold_interval_s: float = 60.0,
        expiring_check_interval_s: float = 3600.0,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._meter = usage_meter
        self._below_interval = below_threshold_interval_s
        self._expiring_interval = expiring_check_interval_s
        self._task: asyncio.Task[None] | None = None
        self._last_expiring_run_date: date | None = None
        self._last_snapshot_date: date | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "InventoryRuleWorker started "
            "(below_threshold=%.0fs, expiring_check=%.0fs)",
            self._below_interval,
            self._expiring_interval,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("InventoryRuleWorker stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("InventoryRuleWorker scan failed")
            await asyncio.sleep(self._below_interval)

    async def run_once(self) -> None:
        """One scan pass — public for tests."""
        async with self._session_factory() as session:
            today = datetime.now(UTC).date()
            await self._scan_below_threshold(session)
            if self._last_expiring_run_date != today:
                await self._scan_expiring_within(session)
                self._last_expiring_run_date = today
            if self._last_snapshot_date != today:
                await self._snapshot_active_stock(session)
                self._last_snapshot_date = today
            await session.commit()

    # ---- below_threshold ----

    async def _scan_below_threshold(self, session: AsyncSession) -> None:
        service = RulesService(session)
        rules = await service.get_active_rules_by_condition_types_all_tenants(
            ["stock.below_threshold"]
        )
        if not rules:
            return
        stock_repo = TimescaleStockItemRepository(session)
        for rule in rules:
            self._meter.record(
                rule.tenant_id, "rule_evaluations", "evaluations"
            )
            rule_evaluations.add(1, {"tenant_id": str(rule.tenant_id)})
            cfg = rule.condition_config
            try:
                product_id = UUID(str(cfg.get("product_id")))
                threshold = int(cfg.get("threshold", 0))
            except (TypeError, ValueError):
                logger.warning("Invalid below_threshold rule config: %s", rule.id)
                continue
            lot_id_raw = cfg.get("lot_id")
            zone_id_raw = cfg.get("zone_id")
            lot_id = UUID(str(lot_id_raw)) if lot_id_raw else None
            zone_id = UUID(str(zone_id_raw)) if zone_id_raw else None

            levels = await stock_repo.stock_levels(
                rule.tenant_id, product_id=product_id, zone_id=zone_id
            )
            # Aggregate matching rows.
            qty = 0
            for row in levels:
                if lot_id is not None and row.lot_id != lot_id:
                    continue
                qty += row.quantity
            if qty >= threshold:
                continue
            await self._fire_alert(
                session,
                service,
                rule,
                severity="warning",
                message=(
                    f"Rule '{rule.name}' triggered: stock for product "
                    f"{product_id} = {qty} (threshold {threshold})"
                ),
                context_extra={
                    "current_quantity": qty,
                    "threshold": threshold,
                    "product_id": str(product_id),
                    "lot_id": str(lot_id) if lot_id else None,
                    "zone_id": str(zone_id) if zone_id else None,
                },
            )

    # ---- expiring_within ----

    async def _scan_expiring_within(self, session: AsyncSession) -> None:
        service = RulesService(session)
        rules = await service.get_active_rules_by_condition_types_all_tenants(
            ["stock.expiring_within"]
        )
        if not rules:
            return
        now = datetime.now(UTC)
        for rule in rules:
            self._meter.record(
                rule.tenant_id, "rule_evaluations", "evaluations"
            )
            rule_evaluations.add(1, {"tenant_id": str(rule.tenant_id)})
            cfg = rule.condition_config
            try:
                days = int(cfg.get("days", 0))
            except (TypeError, ValueError):
                logger.warning("Invalid expiring_within rule config: %s", rule.id)
                continue
            cutoff = now + timedelta(days=days)
            product_id_raw = cfg.get("product_id")
            stmt = select(LotModel).where(
                LotModel.tenant_id == rule.tenant_id,
                LotModel.expires_at.isnot(None),
                LotModel.expires_at <= cutoff,
                LotModel.expires_at >= now,
            )
            if product_id_raw:
                try:
                    product_id = UUID(str(product_id_raw))
                except ValueError:
                    logger.warning(
                        "Invalid product_id in expiring rule %s", rule.id
                    )
                    continue
                stmt = stmt.where(LotModel.product_id == product_id)
            result = await session.execute(stmt)
            lots = list(result.scalars())
            if not lots:
                continue
            await self._fire_alert(
                session,
                service,
                rule,
                severity="warning",
                message=(
                    f"Rule '{rule.name}' triggered: {len(lots)} lot(s) "
                    f"expiring within {days} day(s)"
                ),
                context_extra={
                    "lot_ids": [str(lot.id) for lot in lots],
                    "days": days,
                    "product_id": str(product_id_raw) if product_id_raw else None,
                },
            )

    # ---- metering snapshot ----

    async def _snapshot_active_stock(self, session: AsyncSession) -> None:
        """Record per-tenant ``stock_items_active`` count once per day."""
        stmt = (
            select(StockItemModel.tenant_id, func.count(StockItemModel.id))
            .where(StockItemModel.state == "in_stock")
            .group_by(StockItemModel.tenant_id)
        )
        result = await session.execute(stmt)
        for tenant_id, count in result.all():
            await self._meter.record_snapshot(
                tenant_id, "stock_items_active", "items", int(count)
            )

    # ---- alert fan-out ----

    async def _fire_alert(
        self,
        session: AsyncSession,
        service: RulesService,
        rule: Any,
        *,
        severity: str,
        message: str,
        context_extra: dict[str, Any],
    ) -> None:
        alert = await service.create_alert(
            rule.tenant_id,
            rule.id,
            device_id=None,
            severity=severity,
            message=message,
            context={
                "rule_name": rule.name,
                "condition_type": rule.condition_type,
                "condition_config": rule.condition_config,
                **context_extra,
            },
        )
        self._meter.record(rule.tenant_id, "alerts_fired", "events")
        alerts_fired.add(1, {"tenant_id": str(rule.tenant_id)})
        logger.info(
            "Inventory alert fired: alert=%s rule=%s tenant=%s",
            alert.id,
            rule.id,
            rule.tenant_id,
        )
        await self._event_bus.publish(
            Topic.ALERT_TRIGGERED,
            Event(
                id=alert.id,
                topic=Topic.ALERT_TRIGGERED,
                timestamp=alert.triggered_at,
                payload={
                    "alert_id": str(alert.id),
                    "tenant_id": str(rule.tenant_id),
                    "rule_id": str(rule.id),
                    "device_id": None,
                    "severity": alert.severity,
                    "message": alert.message,
                    "action_type": rule.action_type,
                    "action_config": rule.action_config,
                },
            ),
        )


# Re-export for tests/typing convenience
__all__ = ["InventoryRuleWorker"]


# Quiet unused-import warnings for symbols that exist for forward compatibility.
_ = _uuid
