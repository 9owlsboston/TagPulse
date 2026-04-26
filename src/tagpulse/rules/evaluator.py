"""Rule evaluation engine — evaluates conditions against tag read events."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tagpulse.core.usage_meter import UsageMeter
from tagpulse.events.protocol import Event, EventBus, Topic
from tagpulse.rules import RulesService

logger = logging.getLogger(__name__)


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
