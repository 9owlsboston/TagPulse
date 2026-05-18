"""Unit tests for rule schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.rule_schemas import (
    SIGNALING_VALID_PAIRS,
    RuleCreate,
    RuleUpdate,
    split_signaling_condition_type,
)


class TestRuleCreate:
    def test_valid_threshold(self) -> None:
        rule = RuleCreate(
            name="High signal",
            condition_type="threshold",
            condition_config={"field": "signal_strength", "operator": "gt", "value": -30},
            action_type="webhook",
            action_config={"url": "https://example.com/hook"},
        )
        assert rule.enabled is True

    def test_valid_absence(self) -> None:
        rule = RuleCreate(
            name="Tag missing",
            condition_type="absence",
            condition_config={"tag_id": "TAG001", "minutes": 10},
            action_type="email",
            action_config={"to": "ops@example.com"},
        )
        assert rule.condition_type == "absence"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="",
                condition_type="threshold",
                condition_config={},
                action_type="webhook",
                action_config={},
            )

    def test_invalid_condition_type(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="Bad",
                condition_type="invalid",
                condition_config={},
                action_type="webhook",
                action_config={},
            )

    def test_invalid_action_type(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="Bad",
                condition_type="threshold",
                condition_config={},
                action_type="sms",
                action_config={},
            )


class TestRuleUpdate:
    def test_all_optional(self) -> None:
        patch = RuleUpdate()
        assert patch.model_dump(exclude_unset=True) == {}

    def test_partial(self) -> None:
        patch = RuleUpdate(name="Renamed", enabled=False)
        dumped = patch.model_dump(exclude_unset=True)
        assert dumped == {"name": "Renamed", "enabled": False}


# -- Sprint 41 Phase A: Configurable Signaling Events (ADR-021 v2) --


# The 12 valid (event_type, trigger) pairs flattened to condition_type
# strings. Built from SIGNALING_VALID_PAIRS so the test stays in sync if
# the matrix ever changes.
SIGNALING_VALID_CONDITION_TYPES = sorted(
    f"signaling.{event_type}.{trigger}"
    for event_type, triggers in SIGNALING_VALID_PAIRS.items()
    for trigger in triggers
)


class TestSignalingConditionTypes:
    """ADR-021 v2 §"New condition_type values" — the 12 signaling.*.*
    strings must all parse, and impossible (event_type, trigger) pairs
    plus malformed inputs must be rejected at the Pydantic layer."""

    @pytest.mark.parametrize("condition_type", SIGNALING_VALID_CONDITION_TYPES)
    def test_all_twelve_valid_pairs_accepted(self, condition_type: str) -> None:
        rule = RuleCreate(
            name=f"signaling rule {condition_type}",
            condition_type=condition_type,
            condition_config={},
            action_type="webhook",
            action_config={"url": "https://example.com/hook"},
        )
        assert rule.condition_type == condition_type

    def test_twelve_pairs_is_the_full_matrix(self) -> None:
        # Guard against drift between the regex and SIGNALING_VALID_PAIRS.
        assert len(SIGNALING_VALID_CONDITION_TYPES) == 12

    @pytest.mark.parametrize(
        "condition_type",
        [
            # temperature has no spatial primitives
            "signaling.temperature.on_entry",
            "signaling.temperature.on_exit",
            # geolocation similarly lacks on_entry/on_exit
            "signaling.geolocation.on_entry",
            "signaling.geolocation.on_exit",
            # geolocation has no on_inference (per ADR table)
            "signaling.geolocation.on_inference",
            # temperature has no on_inference (only location does)
            "signaling.temperature.on_inference",
            # geofencing is spatial-only; non-spatial triggers are invalid
            "signaling.geofencing.on_change",
            "signaling.geofencing.periodic",
            "signaling.geofencing.on_inactivity",
            "signaling.geofencing.on_inference",
            # Made-up event types
            "signaling.humidity.on_change",
            "signaling.location.on_explode",
            # Wrong namespace depth
            "signaling.location",
            "signaling.location.on_change.extra",
            # Wrong prefix
            "signal.location.on_change",
        ],
    )
    def test_invalid_pairs_rejected(self, condition_type: str) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="bad signaling rule",
                condition_type=condition_type,
                condition_config={},
                action_type="webhook",
                action_config={"url": "https://example.com/hook"},
            )

    def test_legacy_condition_types_still_accepted(self) -> None:
        # Regression guard for the additive nature of ADR-021 v2 — the
        # original 10 condition types must continue to validate.
        for legacy in (
            "threshold",
            "absence",
            "rate_change",
            "stock.below_threshold",
            "stock.expiring_within",
            "stock.unexpected_in_zone",
            "zone.entered",
            "zone.exited",
            "zone.dwell_exceeded",
            "telemetry.threshold",
        ):
            RuleCreate(
                name=f"legacy {legacy}",
                condition_type=legacy,
                condition_config={},
                action_type="webhook",
                action_config={"url": "https://example.com/hook"},
            )


class TestSplitSignalingConditionType:
    """``split_signaling_condition_type`` is the in-process discriminator
    the evaluator + service use to route a rule's condition_type to its
    (event_type, trigger) handlers without re-parsing strings."""

    @pytest.mark.parametrize(
        ("condition_type", "expected"),
        [
            ("signaling.location.on_change", ("location", "on_change")),
            ("signaling.location.periodic", ("location", "periodic")),
            ("signaling.location.on_inactivity", ("location", "on_inactivity")),
            ("signaling.location.on_inference", ("location", "on_inference")),
            ("signaling.geolocation.on_change", ("geolocation", "on_change")),
            ("signaling.geolocation.periodic", ("geolocation", "periodic")),
            ("signaling.geolocation.on_inactivity", ("geolocation", "on_inactivity")),
            ("signaling.temperature.on_change", ("temperature", "on_change")),
            ("signaling.temperature.periodic", ("temperature", "periodic")),
            ("signaling.temperature.on_inactivity", ("temperature", "on_inactivity")),
            ("signaling.geofencing.on_entry", ("geofencing", "on_entry")),
            ("signaling.geofencing.on_exit", ("geofencing", "on_exit")),
        ],
    )
    def test_valid_pairs_split(self, condition_type: str, expected: tuple[str, str]) -> None:
        assert split_signaling_condition_type(condition_type) == expected

    @pytest.mark.parametrize(
        "condition_type",
        [
            # Legacy condition_types map to "not a signaling event"
            "threshold",
            "absence",
            "zone.entered",
            "telemetry.threshold",
            # Signaling-shaped but invalid pair
            "signaling.temperature.on_entry",
            "signaling.geofencing.on_change",
            "signaling.humidity.on_change",
            # Wrong shape
            "signaling.location",
            "signaling.location.on_change.extra",
            "",
        ],
    )
    def test_non_signaling_returns_none(self, condition_type: str) -> None:
        assert split_signaling_condition_type(condition_type) is None
