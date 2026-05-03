"""Dwell worker (Sprint 17a §5.2).

Periodically scans an in-process map of "subject -> (zone, entered_at)"
populated by ``DwellTracker.on_subject_zone_changed`` and fires synthetic
alerts for ``zone.dwell_exceeded`` rules whose threshold has elapsed.

This is an MVP single-worker implementation. When multi-worker dwell is
needed, an ``asset_current_zone`` table replaces the in-process map per
[docs/design/geofencing-and-map.md §5.2](../../../docs/design/geofencing-and-map.md);
the public worker surface (start/stop/run_once) does not change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.otel_metrics import (
    alerts_fired,
    dwell_alerts_counter,
    dwell_evaluations_counter,
    rule_evaluations,
)
from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.rules import RulesService

logger = logging.getLogger(__name__)


class DwellTracker:
    """In-process subject->zone state populated by SUBJECT_ZONE_CHANGED.

    A separate object so tests can poke state without spinning up the worker.
    """

    def __init__(self, *, max_subjects: int = 50_000) -> None:
        # (tenant_id, subject_id) -> (zone_id, entered_at, subject_kind)
        self._state: dict[
            tuple[UUID, str], tuple[str | None, datetime, str | None]
        ] = {}
        self._max = max_subjects
        # Per (tenant, rule, subject_id) — last alert time, for cooldown.
        self._last_alert: dict[tuple[UUID, UUID, str], datetime] = {}

    async def on_subject_zone_changed(self, event: Event) -> None:
        payload = event.payload
        tenant_id_str = payload.get("tenant_id")
        subject_id_str = payload.get("subject_id")
        if not tenant_id_str or not subject_id_str:
            return
        tenant_id = UUID(tenant_id_str)
        to_zone_id = payload.get("to_zone_id")
        subject_kind = payload.get("subject_kind")
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

    def cooldown_active(
        self, key: tuple[UUID, UUID, str], now: datetime, cooldown_s: int
    ) -> bool:
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
        by_tenant: dict[
            UUID, list[tuple[str, str | None, datetime, str | None]]
        ] = {}
        for tenant_id, subject_id, zone_id, entered_at, subject_kind in snapshot:
            if zone_id is None:
                continue
            by_tenant.setdefault(tenant_id, []).append(
                (subject_id, zone_id, entered_at, subject_kind)
            )
        async with self._session_factory() as session:
            for tenant_id, subjects in by_tenant.items():
                dwell_evaluations_counter.add(
                    len(subjects), {"tenant_id": str(tenant_id)}
                )
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
        rules = await service.get_active_rules_by_condition_type(
            tenant_id, "zone.dwell_exceeded"
        )
        if not rules:
            return
        # Index subjects by zone for fast match against rule.zone_id.
        by_zone: dict[
            str, list[tuple[str, datetime, str | None]]
        ] = {}
        for subject_id, zone_id, entered_at, subject_kind in subjects:
            assert zone_id is not None  # guarded above
            by_zone.setdefault(zone_id, []).append(
                (subject_id, entered_at, subject_kind)
            )
        for rule in rules:
            self._meter.record(tenant_id, "rule_evaluations", "evaluations")
            rule_evaluations.add(1, {"tenant_id": str(tenant_id)})
            zone_id = rule.condition_config.get("zone_id")
            threshold_minutes = int(
                rule.condition_config.get("threshold_minutes", 0)
            )
            if not zone_id or threshold_minutes <= 0:
                continue
            allowed_kinds = rule.condition_config.get("subject_kinds")
            cooldown_s = int(rule.condition_config.get("cooldown_s", 300))
            for subject_id, entered_at, subject_kind in by_zone.get(zone_id, []):
                if (
                    allowed_kinds
                    and subject_kind is not None
                    and subject_kind not in allowed_kinds
                ):
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
