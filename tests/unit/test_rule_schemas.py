"""Unit tests for rule schemas."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tagpulse.models.rule_schemas import (
    SIGNALING_DEFAULT_CAP_PER_SCOPE,
    SIGNALING_VALID_PAIRS,
    RuleCreate,
    RuleResponse,
    RuleUpdate,
    SignalingOnEntryConfig,
    SignalingOnExitConfig,
    SignalingOnInactivityConfig,
    SignalingOnInferenceConfig,
    SignalingOverlappingZonesProcessorConfig,
    SignalingPeriodicConfig,
    split_signaling_condition_type,
    validate_signaling_condition_config,
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


# -- Sprint 41 Phase B: per-trigger condition_config schemas (ADR-021 v2 §"Pydantic schemas") --


class TestSignalingPeriodicConfig:
    @pytest.mark.parametrize("cadence", [1, 5, 60, 1440])
    def test_valid_cadence_minutes(self, cadence: int) -> None:
        config = SignalingPeriodicConfig(cadence_minutes=cadence)
        assert config.cadence_minutes == cadence

    @pytest.mark.parametrize("cadence", [0, -1, 1441, 100_000])
    def test_out_of_bounds_cadence_rejected(self, cadence: int) -> None:
        with pytest.raises(ValidationError):
            SignalingPeriodicConfig(cadence_minutes=cadence)


class TestSignalingOnInactivityConfig:
    def test_default_cooldown(self) -> None:
        config = SignalingOnInactivityConfig(inactivity_minutes=10)
        assert config.cooldown_s == 300

    @pytest.mark.parametrize("minutes", [0, -1, 1441])
    def test_invalid_inactivity_minutes(self, minutes: int) -> None:
        with pytest.raises(ValidationError):
            SignalingOnInactivityConfig(inactivity_minutes=minutes)


class TestSignalingOnInferenceConfig:
    @pytest.mark.parametrize("conf", [0.0, 0.5, 1.0])
    def test_valid_confidence(self, conf: float) -> None:
        config = SignalingOnInferenceConfig(min_confidence=conf)
        assert config.min_confidence == conf

    @pytest.mark.parametrize("conf", [-0.1, 1.01, 2.0])
    def test_out_of_range_confidence(self, conf: float) -> None:
        with pytest.raises(ValidationError):
            SignalingOnInferenceConfig(min_confidence=conf)


class TestSignalingOnEntryExitConfig:
    def test_entry_requires_zone(self) -> None:
        with pytest.raises(ValidationError):
            SignalingOnEntryConfig()  # type: ignore[call-arg]

    def test_exit_requires_zone(self) -> None:
        with pytest.raises(ValidationError):
            SignalingOnExitConfig()  # type: ignore[call-arg]

    def test_entry_subject_kinds_optional(self) -> None:
        config = SignalingOnEntryConfig(zone_id="zone-a")
        assert config.subject_kinds is None
        assert config.cooldown_s == 60


class TestSignalingOverlappingZonesProcessorConfig:
    @pytest.mark.parametrize("window", [30, 60, 300, 1800])
    def test_valid_window(self, window: int) -> None:
        config = SignalingOverlappingZonesProcessorConfig(aggregation_window_s=window)  # type: ignore[arg-type]
        assert config.aggregation_window_s == window

    @pytest.mark.parametrize("window", [15, 45, 120, 600, 3600])
    def test_window_outside_enum_rejected(self, window: int) -> None:
        with pytest.raises(ValidationError):
            SignalingOverlappingZonesProcessorConfig(aggregation_window_s=window)  # type: ignore[arg-type]

    def test_aging_weight_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SignalingOverlappingZonesProcessorConfig(aggregation_window_s=60, aging_weight=1.5)


class TestValidateSignalingConditionConfig:
    """The service-layer dispatcher that picks the right trigger config
    schema based on a rule's condition_type. Returns the parsed model
    on success, ``None`` for legacy condition_types, and raises
    ``ValidationError`` when the payload doesn't fit the trigger's
    shape."""

    def test_legacy_returns_none(self) -> None:
        assert validate_signaling_condition_config("threshold", {}) is None
        assert validate_signaling_condition_config("zone.entered", {"zone_id": "z"}) is None

    def test_periodic_valid(self) -> None:
        result = validate_signaling_condition_config(
            "signaling.location.periodic", {"cadence_minutes": 5}
        )
        assert isinstance(result, SignalingPeriodicConfig)
        assert result.cadence_minutes == 5

    def test_periodic_rejects_missing_cadence(self) -> None:
        with pytest.raises(ValidationError):
            validate_signaling_condition_config("signaling.location.periodic", {})

    def test_on_inactivity_valid(self) -> None:
        result = validate_signaling_condition_config(
            "signaling.temperature.on_inactivity", {"inactivity_minutes": 15}
        )
        assert isinstance(result, SignalingOnInactivityConfig)

    def test_on_inference_defaults(self) -> None:
        result = validate_signaling_condition_config("signaling.location.on_inference", {})
        assert isinstance(result, SignalingOnInferenceConfig)
        assert result.min_confidence == 0.0

    def test_on_entry_requires_zone(self) -> None:
        with pytest.raises(ValidationError):
            validate_signaling_condition_config("signaling.geofencing.on_entry", {})

    def test_on_change_valid(self) -> None:
        # on_change accepts the legacy empty config (no zone, no delta)
        # because event_type-specific shapes are intentionally permissive
        # and applied by the evaluator.
        validate_signaling_condition_config("signaling.location.on_change", {"cooldown_s": 30})


# -- Sprint 41 Phase B: RuleCreate / RuleUpdate / RuleResponse new fields --


class TestRuleCreateSignalingFields:
    """Signaling-rule scoping fields on ``RuleCreate``: cross-field
    validator must auto-populate event_type+trigger from a signaling
    condition_type, reject mismatched values, and reject signaling
    metadata on legacy condition_types."""

    def test_signaling_condition_type_auto_populates_event_type_and_trigger(self) -> None:
        rule = RuleCreate(
            name="r1",
            condition_type="signaling.location.periodic",
            condition_config={"cadence_minutes": 5},
            action_type="webhook",
            action_config={"url": "https://example.com/h"},
        )
        assert rule.event_type == "location"
        assert rule.trigger == "periodic"

    def test_signaling_condition_type_with_matching_explicit_fields(self) -> None:
        rule = RuleCreate(
            name="r1",
            condition_type="signaling.temperature.on_change",
            condition_config={"min_delta": 2.0},
            action_type="webhook",
            action_config={"url": "https://example.com/h"},
            event_type="temperature",
            trigger="on_change",
        )
        assert rule.event_type == "temperature"

    def test_mismatched_event_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="r1",
                condition_type="signaling.location.periodic",
                condition_config={"cadence_minutes": 5},
                action_type="webhook",
                action_config={"url": "https://example.com/h"},
                event_type="temperature",  # ← contradicts the condition_type
            )

    def test_mismatched_trigger_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="r1",
                condition_type="signaling.location.periodic",
                condition_config={"cadence_minutes": 5},
                action_type="webhook",
                action_config={"url": "https://example.com/h"},
                trigger="on_change",  # ← contradicts the condition_type
            )

    def test_legacy_condition_type_rejects_event_type(self) -> None:
        with pytest.raises(ValidationError):
            RuleCreate(
                name="r1",
                condition_type="threshold",
                condition_config={},
                action_type="webhook",
                action_config={"url": "https://example.com/h"},
                event_type="location",
            )

    def test_legacy_condition_type_leaves_signaling_fields_none(self) -> None:
        rule = RuleCreate(
            name="legacy",
            condition_type="threshold",
            condition_config={},
            action_type="webhook",
            action_config={"url": "https://example.com/h"},
        )
        assert rule.event_type is None
        assert rule.trigger is None
        assert rule.category_ids == []
        assert rule.confidence_threshold == Decimal("0.0")

    def test_signaling_category_ids_round_trip(self) -> None:
        cat_a, cat_b = uuid4(), uuid4()
        rule = RuleCreate(
            name="r",
            condition_type="signaling.location.periodic",
            condition_config={"cadence_minutes": 5},
            action_type="notification",
            action_config={},
            category_ids=[cat_a, cat_b],
        )
        assert rule.category_ids == [cat_a, cat_b]


class TestRuleResponseKind:
    """``kind`` is the computed discriminator the UI uses to render
    signaling vs legacy rules differently. It must be derived from
    ``event_type`` regardless of what the caller sends in."""

    def _make_response(self, *, event_type: str | None) -> RuleResponse:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return RuleResponse(
            id=uuid4(),
            tenant_id=uuid4(),
            name="r",
            description=None,
            condition_type="signaling.location.periodic" if event_type else "threshold",
            condition_config={},
            action_type="webhook",
            action_config={"url": "https://example.com/h"},
            scope_device_id=None,
            enabled=True,
            created_at=now,
            updated_at=now,
            event_type=event_type,
            trigger="periodic" if event_type else None,
        )

    def test_kind_signaling_when_event_type_present(self) -> None:
        response = self._make_response(event_type="location")
        assert response.kind == "signaling"

    def test_kind_legacy_when_event_type_none(self) -> None:
        response = self._make_response(event_type=None)
        assert response.kind == "legacy"

    def test_kind_recomputed_overrides_caller(self) -> None:
        # Even if a caller explicitly sets kind=legacy on a rule with
        # event_type=temperature, the model_validator must enforce
        # consistency to avoid misleading clients.
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        response = RuleResponse(
            id=uuid4(),
            tenant_id=uuid4(),
            name="r",
            description=None,
            condition_type="signaling.temperature.on_change",
            condition_config={},
            action_type="webhook",
            action_config={"url": "https://example.com/h"},
            scope_device_id=None,
            enabled=True,
            created_at=now,
            updated_at=now,
            event_type="temperature",
            trigger="on_change",
            kind="legacy",  # ← caller lies
        )
        assert response.kind == "signaling"


class TestRuleUpdateSignalingFields:
    """``RuleUpdate`` doesn't run the cross-field validator (the service
    layer reconciles against the existing row) but the per-field
    pattern guards must still reject malformed values."""

    def test_event_type_pattern_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RuleUpdate(event_type="humidity")

    def test_trigger_pattern_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RuleUpdate(trigger="on_explode")

    def test_processor_pattern_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RuleUpdate(processor="bayes_net")

    def test_confidence_threshold_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RuleUpdate(confidence_threshold=Decimal("1.5"))


def test_signaling_default_cap_is_five() -> None:
    """Guard rail \u2014 ADR-021 v2 open question #4 fixes the default at 5.
    A change here is a deliberate ADR amendment, not a refactor."""

    assert SIGNALING_DEFAULT_CAP_PER_SCOPE == 5
