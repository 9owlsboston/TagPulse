"""Unit tests for rule schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.rule_schemas import (
    SENSING_VALID_PAIRS,
    RuleCreate,
    RuleUpdate,
    split_sensing_condition_type,
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


# -- Sprint 41 Phase A: Configurable Sensing Events (ADR-021 v2) --


# The 12 valid (event_type, trigger) pairs flattened to condition_type
# strings. Built from SENSING_VALID_PAIRS so the test stays in sync if
# the matrix ever changes.
SENSING_VALID_CONDITION_TYPES = sorted(
    f"sensing.{event_type}.{trigger}"
    for event_type, triggers in SENSING_VALID_PAIRS.items()
    for trigger in triggers
)


class TestSensingConditionTypes:
    """ADR-021 v2 §"New condition_type values" — the 12 sensing.*.*
    strings must all parse, and impossible (event_type, trigger) pairs
    plus malformed inputs must be rejected at the Pydantic layer."""

    @pytest.mark.parametrize("condition_type", SENSING_VALID_CONDITION_TYPES)
    def test_all_twelve_valid_pairs_accepted(self, condition_type: str) -> None:
        rule = RuleCreate(
            name=f"sensing rule {condition_type}",
            condition_type=condition_type,
            condition_config={},
            action_type="webhook",
            action_config={"url": "https://example.com/hook"},
        )
        assert rule.condition_type == condition_type

    def test_twelve_pairs_is_the_full_matrix(self) -> None:
        # Guard against drift between the regex and SENSING_VALID_PAIRS.
        assert len(SENSING_VALID_CONDITION_TYPES) == 12

    @pytest.mark.parametrize(
        "condition_type",
        [
            # temperature has no spatial primitives
            "sensing.temperature.on_entry",
            "sensing.temperature.on_exit",
            # geolocation similarly lacks on_entry/on_exit
            "sensing.geolocation.on_entry",
            "sensing.geolocation.on_exit",
            # geolocation has no on_inference (per ADR table)
            "sensing.geolocation.on_inference",
            # temperature has no on_inference (only location does)
            "sensing.temperature.on_inference",
            # geofencing is spatial-only; non-spatial triggers are invalid
            "sensing.geofencing.on_change",
            "sensing.geofencing.periodic",
            "sensing.geofencing.on_inactivity",
            "sensing.geofencing.on_inference",
            # Made-up event types
            "sensing.humidity.on_change",
            "sensing.location.on_explode",
            # Wrong namespace depth
            "sensing.location",
            "sensing.location.on_change.extra",
            # Wrong prefix
            "sense.location.on_change",
        ],
    )
    def test_invalid_pairs_rejected(self, condition_type: str) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="bad sensing rule",
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


class TestSplitSensingConditionType:
    """``split_sensing_condition_type`` is the in-process discriminator
    the evaluator + service use to route a rule's condition_type to its
    (event_type, trigger) handlers without re-parsing strings."""

    @pytest.mark.parametrize(
        ("condition_type", "expected"),
        [
            ("sensing.location.on_change", ("location", "on_change")),
            ("sensing.location.periodic", ("location", "periodic")),
            ("sensing.location.on_inactivity", ("location", "on_inactivity")),
            ("sensing.location.on_inference", ("location", "on_inference")),
            ("sensing.geolocation.on_change", ("geolocation", "on_change")),
            ("sensing.geolocation.periodic", ("geolocation", "periodic")),
            ("sensing.geolocation.on_inactivity", ("geolocation", "on_inactivity")),
            ("sensing.temperature.on_change", ("temperature", "on_change")),
            ("sensing.temperature.periodic", ("temperature", "periodic")),
            ("sensing.temperature.on_inactivity", ("temperature", "on_inactivity")),
            ("sensing.geofencing.on_entry", ("geofencing", "on_entry")),
            ("sensing.geofencing.on_exit", ("geofencing", "on_exit")),
        ],
    )
    def test_valid_pairs_split(self, condition_type: str, expected: tuple[str, str]) -> None:
        assert split_sensing_condition_type(condition_type) == expected

    @pytest.mark.parametrize(
        "condition_type",
        [
            # Legacy condition_types map to "not a sensing event"
            "threshold",
            "absence",
            "zone.entered",
            "telemetry.threshold",
            # Sensing-shaped but invalid pair
            "sensing.temperature.on_entry",
            "sensing.geofencing.on_change",
            "sensing.humidity.on_change",
            # Wrong shape
            "sensing.location",
            "sensing.location.on_change.extra",
            "",
        ],
    )
    def test_non_sensing_returns_none(self, condition_type: str) -> None:
        assert split_sensing_condition_type(condition_type) is None
