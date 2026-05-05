"""Rule evaluation engine — evaluates conditions against tag read events."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.models.database import AssetModel, ZoneModel
from tagpulse.rules import RulesService

logger = logging.getLogger(__name__)


# In-process cooldown table for zone.entered / zone.exited rules per Sprint 17a.
# Entry value is the UTC datetime *until which* further alerts are suppressed
# for the (tenant, rule, subject) tuple. Bounded to avoid unbounded growth on
# long-running workers; oldest entry evicted on overflow.
_RULE_COOLDOWN_MAX = 16_384
_RULE_COOLDOWN_UNTIL: dict[tuple[UUID, UUID, str], datetime] = {}


def _cooldown_active(key: tuple[UUID, UUID, str], now: datetime) -> bool:
    expires = _RULE_COOLDOWN_UNTIL.get(key)
    if expires is None:
        return False
    if expires <= now:
        _RULE_COOLDOWN_UNTIL.pop(key, None)
        return False
    return True


def _set_cooldown(
    key: tuple[UUID, UUID, str], now: datetime, seconds: int
) -> None:
    if seconds <= 0:
        return
    if (
        key not in _RULE_COOLDOWN_UNTIL
        and len(_RULE_COOLDOWN_UNTIL) >= _RULE_COOLDOWN_MAX
    ):
        try:
            oldest = next(iter(_RULE_COOLDOWN_UNTIL))
            _RULE_COOLDOWN_UNTIL.pop(oldest, None)
        except StopIteration:  # pragma: no cover — defensive
            pass
    _RULE_COOLDOWN_UNTIL[key] = now + timedelta(seconds=seconds)


class RuleEvaluator:
    """Evaluates active rules against incoming tag read events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        usage_meter: UsageMeter,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._meter = usage_meter

    async def on_tag_read(self, event: Event) -> None:
        """Called by EventBus when a tag read is created."""
        payload = event.payload
        tenant_id_str = payload.get("tenant_id")
        device_id_str = payload.get("device_id")
        if not tenant_id_str or not device_id_str:
            return

        tenant_id = UUID(tenant_id_str)
        device_id = UUID(device_id_str)

        async with self._session_factory() as session:
            service = RulesService(session)
            rules = await service.get_active_rules_for_device(tenant_id, device_id)

            for rule in rules:
                self._meter.record(tenant_id, "rule_evaluations", "evaluations")
                matched = _evaluate_condition(
                    rule.condition_type, rule.condition_config, payload
                )
                if matched:
                    message = (
                        f"Rule '{rule.name}' triggered: "
                        f"{rule.condition_type} condition met"
                    )
                    alert = await service.create_alert(
                        tenant_id,
                        rule.id,
                        device_id=device_id,
                        severity="warning",
                        message=message,
                        context={
                            "rule_name": rule.name,
                            "condition_type": rule.condition_type,
                            "condition_config": rule.condition_config,
                            "event_payload": payload,
                        },
                    )
                    self._meter.record(tenant_id, "alerts_fired", "events")
                    logger.info(
                        "Alert fired: alert=%s rule=%s device=%s",
                        alert.id,
                        rule.id,
                        device_id,
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
                                "device_id": str(device_id),
                                "severity": alert.severity,
                                "message": alert.message,
                                "action_type": rule.action_type,
                                "action_config": rule.action_config,
                            },
                        ),
                    )
            await session.commit()

    async def on_subject_zone_changed(self, event: Event) -> None:
        """Evaluate zone-transition rules on ``Topic.SUBJECT_ZONE_CHANGED``.

        Handles three condition families:

        - ``stock.unexpected_in_zone`` — Phase E (stock_item only).
        - ``zone.entered`` / ``zone.exited`` — Sprint 17a; any subject_kind.

        ``zone.dwell_exceeded`` is NOT evaluated here — that's a periodic
        check fired by ``DwellWorker``.
        """
        payload = event.payload
        tenant_id_str = payload.get("tenant_id")
        if not tenant_id_str:
            return
        tenant_id = UUID(tenant_id_str)
        subject_kind = payload.get("subject_kind")
        subject_id_str = payload.get("subject_id")
        from_zone_id = payload.get("from_zone_id")
        to_zone_id = payload.get("to_zone_id")

        async with self._session_factory() as session:
            service = RulesService(session)

            # 1) Generic enter/exit rules (Sprint 17a).
            if subject_id_str:
                await self._eval_zone_entered_exited(
                    session=session,
                    service=service,
                    tenant_id=tenant_id,
                    subject_id_str=subject_id_str,
                    subject_kind=subject_kind,
                    from_zone_id=from_zone_id,
                    to_zone_id=to_zone_id,
                    payload=payload,
                )

            # 2) Phase E stock-item rules.
            if subject_kind == "stock_item" and to_zone_id is not None:
                await self._eval_stock_unexpected_in_zone(
                    session=session,
                    service=service,
                    tenant_id=tenant_id,
                    payload=payload,
                    subject_id_str=subject_id_str,
                    to_zone_id=to_zone_id,
                )

            await session.commit()

    async def _lookup_subject_name(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        subject_kind: str | None,
        subject_id_str: str,
    ) -> str | None:
        """Look up a subject's display name for use in alert messages.

        Currently only resolves ``asset`` subjects (the only kind that has a
        user-meaningful name in the Sprint 17a smoke test). Returns ``None``
        on any lookup failure so callers fall back to the raw UUID.
        """
        if subject_kind != "asset":
            return None
        try:
            sid = UUID(subject_id_str)
        except ValueError:
            return None
        stmt = select(AssetModel.name).where(
            AssetModel.id == sid, AssetModel.tenant_id == tenant_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _lookup_zone_name(
        self,
        session: AsyncSession,
        tenant_id: UUID,
        zone_id_str: str,
    ) -> str | None:
        try:
            zid = UUID(zone_id_str)
        except ValueError:
            return None
        stmt = select(ZoneModel.name).where(
            ZoneModel.id == zid, ZoneModel.tenant_id == tenant_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _eval_zone_entered_exited(
        self,
        *,
        session: AsyncSession,
        service: RulesService,
        tenant_id: UUID,
        subject_id_str: str,
        subject_kind: str | None,
        from_zone_id: str | None,
        to_zone_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)
        # Look up subject + zone display names once so alert messages are
        # human-readable in the UI (e.g. "asset 'Sim-Pallet-03' entered zone
        # 'Bay Area West Block'" instead of two opaque UUIDs).
        subject_name = await self._lookup_subject_name(
            session, tenant_id, subject_kind, subject_id_str
        )
        zone_name_cache: dict[str, str | None] = {}
        # Run both condition types in one pass to keep the DB roundtrips low.
        for condition_type, target_zone in (
            ("zone.entered", to_zone_id),
            ("zone.exited", from_zone_id),
        ):
            if target_zone is None:
                continue
            rules = await service.get_active_rules_by_condition_type(
                tenant_id, condition_type
            )
            for rule in rules:
                self._meter.record(tenant_id, "rule_evaluations", "evaluations")
                if rule.condition_config.get("zone_id") != target_zone:
                    continue
                allowed_kinds = rule.condition_config.get("subject_kinds")
                if (
                    allowed_kinds
                    and subject_kind is not None
                    and subject_kind not in allowed_kinds
                ):
                    continue
                cooldown_key = (tenant_id, rule.id, subject_id_str)
                if _cooldown_active(cooldown_key, now):
                    continue
                cooldown_s = int(rule.condition_config.get("cooldown_s", 60))
                _set_cooldown(cooldown_key, now, cooldown_s)

                verb = "entered" if condition_type == "zone.entered" else "exited"
                if target_zone not in zone_name_cache:
                    zone_name_cache[target_zone] = await self._lookup_zone_name(
                        session, tenant_id, target_zone
                    )
                zone_label = zone_name_cache[target_zone] or target_zone
                subject_label = subject_name or subject_id_str
                kind_label = subject_kind or "subject"
                message = (
                    f"Rule '{rule.name}' triggered: {kind_label} "
                    f"'{subject_label}' ({subject_id_str}) {verb} zone "
                    f"'{zone_label}' ({target_zone})"
                )
                alert = await service.create_alert(
                    tenant_id,
                    rule.id,
                    device_id=None,
                    severity="info",
                    message=message,
                    context={
                        "rule_name": rule.name,
                        "condition_type": rule.condition_type,
                        "condition_config": rule.condition_config,
                        "event_payload": payload,
                    },
                )
                self._meter.record(tenant_id, "alerts_fired", "events")
                logger.info(
                    "Zone alert fired: alert=%s rule=%s subject=%s zone=%s "
                    "verb=%s",
                    alert.id,
                    rule.id,
                    subject_id_str,
                    target_zone,
                    verb,
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

    async def _eval_stock_unexpected_in_zone(
        self,
        *,
        session: AsyncSession,
        service: RulesService,
        tenant_id: UUID,
        payload: dict[str, Any],
        subject_id_str: str | None,
        to_zone_id: str,
    ) -> None:
        product_id_str = payload.get("product_id")
        rules = await service.get_active_rules_by_condition_type(
            tenant_id, "stock.unexpected_in_zone"
        )
        zone_name = await self._lookup_zone_name(session, tenant_id, to_zone_id)
        zone_label = zone_name or to_zone_id
        subject_label = subject_id_str or "<unknown>"
        for rule in rules:
            self._meter.record(tenant_id, "rule_evaluations", "evaluations")
            config_product = rule.condition_config.get("product_id")
            if config_product and config_product != product_id_str:
                continue
            allowed = set(rule.condition_config.get("allowed_zone_ids") or [])
            if not allowed:
                continue
            if to_zone_id in allowed:
                continue
            message = (
                f"Rule '{rule.name}' triggered: stock_item "
                f"'{subject_label}' entered zone "
                f"'{zone_label}' ({to_zone_id}) (not in allowed list)"
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
                    "event_payload": payload,
                },
            )
            self._meter.record(tenant_id, "alerts_fired", "events")
            logger.info(
                "Stock zone alert fired: alert=%s rule=%s stock_item=%s zone=%s",
                alert.id,
                rule.id,
                subject_id_str,
                to_zone_id,
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


def _evaluate_condition(
    condition_type: str,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """Evaluate a rule condition against event payload."""
    if condition_type == "threshold":
        return _eval_threshold(config, payload)
    if condition_type == "rate_change":
        return _eval_rate_change(config, payload)
    if condition_type == "absence":
        return _eval_absence(config, payload)
    return False


def _eval_threshold(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Evaluate threshold condition: field <op> value."""
    field = config.get("field", "")
    operator = config.get("operator", "")
    threshold = config.get("value")
    if threshold is None:
        return False

    actual = payload.get(field)
    if actual is None:
        return False

    try:
        actual_f = float(actual)
        threshold_f = float(threshold)
    except (ValueError, TypeError):
        return False

    if operator == "gt":
        return actual_f > threshold_f
    if operator == "lt":
        return actual_f < threshold_f
    if operator == "gte":
        return actual_f >= threshold_f
    if operator == "lte":
        return actual_f <= threshold_f
    if operator == "eq":
        return actual_f == threshold_f
    return False


def _eval_absence(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Evaluate absence condition: alert if specific tag_id is seen.

    Absence detection works by inversion: the rule fires when a tag read
    arrives for a *different* tag than the one being monitored, after the
    configured absence window. Full timer-based absence detection requires
    a background scheduler; this inline check triggers when the monitored
    tag_id is NOT the one in the current event, acting as a signal that
    the expected tag is absent from recent reads.
    """
    monitored_tag = config.get("tag_id")
    if not monitored_tag:
        return False
    current_tag = payload.get("tag_id")
    return bool(current_tag != monitored_tag)


def _eval_rate_change(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Evaluate rate change condition based on signal strength deviation.

    Full rate-of-change detection requires historical query (reads/min over
    window). This inline approximation checks if the current signal strength
    deviates from a baseline by more than the configured percentage.
    """
    change_percent = config.get("change_percent")
    if change_percent is None:
        return False

    signal = payload.get("signal_strength")
    if signal is None:
        return False

    try:
        signal_f = float(signal)
        change_f = float(change_percent)
    except (ValueError, TypeError):
        return False

    # Use -50 dBm as a nominal baseline for RFID readers
    baseline = config.get("baseline", -50.0)
    try:
        baseline_f = float(baseline)
    except (ValueError, TypeError):
        return False

    if baseline_f == 0:
        return False

    deviation = abs((signal_f - baseline_f) / baseline_f) * 100
    return deviation > change_f
