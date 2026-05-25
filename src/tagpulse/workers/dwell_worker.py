"""Dwell worker (Sprint 17a §5.2).

Periodically scans an in-process map of "subject -> (zone, entered_at)"
populated by ``DwellTracker.on_subject_zone_changed`` and fires synthetic
alerts for ``zone.dwell_exceeded`` rules whose threshold has elapsed.

The in-process map is **write-through** to the ``subject_current_zone`` table
(migration 027); on startup ``DwellTracker.hydrate()`` rebuilds the map from
the table so worker restarts (and multi-worker deployments) don't lose dwell
state. The fast path stays in-process — DB writes happen on every event but
do not block the publisher (the tracker is invoked from the event-bus
subscriber, which already runs out-of-band of the ingest hot path).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.otel_metrics import (
    alerts_fired,
    dwell_alerts_counter,
    dwell_evaluations_counter,
    rule_evaluations,
)
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.database import SubjectCurrentZoneModel
from tagpulse.rules import RulesService

logger = logging.getLogger(__name__)


class DwellTracker:
    """In-process subject->zone state populated by SUBJECT_ZONE_CHANGED.

    Write-through to ``subject_current_zone`` (migration 027) for durability.
    The in-process map is the read path; the table is the persistence backing.
    """

    def __init__(
        self,
        *,
        max_subjects: int = 50_000,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        # (tenant_id, subject_id) -> (zone_id, entered_at, subject_kind)
        self._state: dict[tuple[UUID, str], tuple[str | None, datetime, str | None]] = {}
        self._max = max_subjects
        # Per (tenant, rule, subject_id) — last alert time, for cooldown.
        self._last_alert: dict[tuple[UUID, UUID, str], datetime] = {}
        self._session_factory = session_factory

    async def on_subject_zone_changed(self, event: Event) -> None:
        payload = event.payload
        tenant_id_str = payload.get("tenant_id")
        subject_id_str = payload.get("subject_id")
        if not tenant_id_str or not subject_id_str:
            return
        tenant_id = UUID(tenant_id_str)
        to_zone_id = payload.get("to_zone_id")
        subject_kind = payload.get("subject_kind")
        zone_kind = payload.get("zone_kind")
        ts_raw = payload.get("timestamp")
        if isinstance(ts_raw, str):
            try:
                entered_at = datetime.fromisoformat(ts_raw)
            except ValueError:
                entered_at = datetime.now(UTC)
        else:
            entered_at = datetime.now(UTC)
        key = (tenant_id, subject_id_str)
        if key not in self._state and len(self._state) >= self._max:
            try:
                oldest = next(iter(self._state))
                self._state.pop(oldest, None)
            except StopIteration:  # pragma: no cover
                pass
        self._state[key] = (to_zone_id, entered_at, subject_kind)
        # Write-through persistence (Sprint 17a §5.2).
        if self._session_factory is not None:
            try:
                await self._persist(
                    tenant_id=tenant_id,
                    subject_kind=subject_kind,
                    subject_id=subject_id_str,
                    zone_id=to_zone_id,
                    zone_kind=zone_kind,
                    entered_at=entered_at,
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("DwellTracker persist failed")

    async def _persist(
        self,
        *,
        tenant_id: UUID,
        subject_kind: str | None,
        subject_id: str,
        zone_id: str | None,
        zone_kind: str | None,
        entered_at: datetime,
    ) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            stmt = pg_insert(SubjectCurrentZoneModel).values(
                tenant_id=tenant_id,
                subject_kind=subject_kind or "unknown",
                subject_id=UUID(subject_id),
                zone_id=UUID(zone_id) if zone_id else None,
                zone_kind=zone_kind,
                entered_at=entered_at,
                updated_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["tenant_id", "subject_kind", "subject_id"],
                set_={
                    "zone_id": stmt.excluded.zone_id,
                    "zone_kind": stmt.excluded.zone_kind,
                    "entered_at": stmt.excluded.entered_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def hydrate(self) -> int:
        """Reload in-process state from ``subject_current_zone``.

        Called once on app startup so dwell tracking survives restart. Returns
        the number of rows loaded. No-op if no session factory is configured.
        """
        if self._session_factory is None:
            return 0
        async with self._session_factory() as session:
            stmt = select(SubjectCurrentZoneModel).limit(self._max)
            result = await session.execute(stmt)
            count = 0
            for row in result.scalars():
                key = (row.tenant_id, str(row.subject_id))
                self._state[key] = (
                    str(row.zone_id) if row.zone_id else None,
                    row.entered_at,
                    row.subject_kind,
                )
                count += 1
            logger.info("DwellTracker hydrated %d rows", count)
            return count

    def snapshot(
        self,
    ) -> list[tuple[UUID, str, str | None, datetime, str | None]]:
        return [
            (tenant_id, subject_id, zone_id, entered_at, subject_kind)
            for (tenant_id, subject_id), (
                zone_id,
                entered_at,
                subject_kind,
            ) in self._state.items()
        ]

    def cooldown_active(self, key: tuple[UUID, UUID, str], now: datetime, cooldown_s: int) -> bool:
        last = self._last_alert.get(key)
        if last is None:
            return False
        return now - last < timedelta(seconds=cooldown_s)

    def mark_alert(self, key: tuple[UUID, UUID, str], now: datetime) -> None:
        self._last_alert[key] = now


class DwellWorker:
    """Periodic ``zone.dwell_exceeded`` evaluator."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        usage_meter: UsageMeter,
        tracker: DwellTracker,
        *,
        interval_s: float = 60.0,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._meter = usage_meter
        self._tracker = tracker
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("DwellWorker started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("DwellWorker stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("DwellWorker scan failed")
            await asyncio.sleep(self._interval)

    async def run_once(self) -> None:
        snapshot = self._tracker.snapshot()
        if not snapshot:
            return
        now = datetime.now(UTC)
        # Group by tenant for fewer DB roundtrips.
        by_tenant: dict[UUID, list[tuple[str, str | None, datetime, str | None]]] = {}
        for tenant_id, subject_id, zone_id, entered_at, subject_kind in snapshot:
            if zone_id is None:
                continue
            by_tenant.setdefault(tenant_id, []).append(
                (subject_id, zone_id, entered_at, subject_kind)
            )
        async with self._session_factory() as session:
            for tenant_id, subjects in by_tenant.items():
                dwell_evaluations_counter.add(len(subjects), {"tenant_id": str(tenant_id)})
                await self._eval_tenant(session, tenant_id, subjects, now)
            await session.commit()

    async def _eval_tenant(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        subjects: list[tuple[str, str | None, datetime, str | None]],
        now: datetime,
    ) -> None:
        service = RulesService(session)
        rules = await service.get_active_rules_by_condition_type(tenant_id, "zone.dwell_exceeded")
        if not rules:
            return
        # Index subjects by zone for fast match against rule.zone_id.
        by_zone: dict[str, list[tuple[str, datetime, str | None]]] = {}
        for subject_id, zone_id, entered_at, subject_kind in subjects:
            assert zone_id is not None  # guarded above
            by_zone.setdefault(zone_id, []).append((subject_id, entered_at, subject_kind))
        for rule in rules:
            self._meter.record(tenant_id, "rule_evaluations", "evaluations")
            rule_evaluations.add(1, {"tenant_id": str(tenant_id)})
            zone_id = rule.condition_config.get("zone_id")
            threshold_minutes = int(rule.condition_config.get("threshold_minutes", 0))
            if not zone_id or threshold_minutes <= 0:
                continue
            allowed_kinds = rule.condition_config.get("subject_kinds")
            cooldown_s = int(rule.condition_config.get("cooldown_s", 300))
            for subject_id, entered_at, subject_kind in by_zone.get(zone_id, []):
                if allowed_kinds and subject_kind is not None and subject_kind not in allowed_kinds:
                    continue
                if now - entered_at < timedelta(minutes=threshold_minutes):
                    continue
                cooldown_key = (tenant_id, rule.id, subject_id)
                if self._tracker.cooldown_active(cooldown_key, now, cooldown_s):
                    continue
                self._tracker.mark_alert(cooldown_key, now)
                await self._fire(
                    session=session,
                    service=service,
                    rule=rule,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    subject_kind=subject_kind,
                    zone_id=zone_id,
                    entered_at=entered_at,
                    now=now,
                )

    async def _fire(
        self,
        *,
        session: AsyncSession,
        service: RulesService,
        rule: Any,
        tenant_id: UUID,
        subject_id: str,
        subject_kind: str | None,
        zone_id: str,
        entered_at: datetime,
        now: datetime,
    ) -> None:
        elapsed_min = (now - entered_at).total_seconds() / 60.0
        message = (
            f"Rule '{rule.name}' triggered: {subject_kind or 'subject'} "
            f"{subject_id} dwelt in zone {zone_id} for "
            f"{elapsed_min:.1f} minutes"
        )
        alert = await service.create_alert(
            tenant_id,
            rule.id,
            device_id=None,
            severity="warning",
            message=message,
            context={
                "rule_name": rule.name,
                "condition_type": rule.condition_type,
                "condition_config": rule.condition_config,
                "subject_id": subject_id,
                "subject_kind": subject_kind,
                "zone_id": zone_id,
                "entered_at": entered_at.isoformat(),
                "evaluated_at": now.isoformat(),
                "dwell_minutes": elapsed_min,
            },
        )
        self._meter.record(tenant_id, "alerts_fired", "events")
        alerts_fired.add(1, {"tenant_id": str(tenant_id)})
        dwell_alerts_counter.add(1, {"tenant_id": str(tenant_id)})
        logger.info(
            "Dwell alert fired: alert=%s rule=%s subject=%s zone=%s minutes=%.1f",
            alert.id,
            rule.id,
            subject_id,
            zone_id,
            elapsed_min,
        )
        await self._event_bus.publish(
            Topic.ALERT_TRIGGERED,
            Event(
                id=alert.id,
                topic=Topic.ALERT_TRIGGERED,
                timestamp=alert.triggered_at,
                payload={
                    "alert_id": str(alert.id),
                    "tenant_id": str(tenant_id),
                    "rule_id": str(rule.id),
                    "device_id": None,
                    "severity": alert.severity,
                    "message": alert.message,
                    "action_type": rule.action_type,
                    "action_config": rule.action_config,
                },
            ),
        )


__all__ = ["DwellTracker", "DwellWorker"]
