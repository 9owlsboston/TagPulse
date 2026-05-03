"""Unit tests for the rule_schemas zone-condition extensions (Sprint 17a)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.models.rule_schemas import (
    RuleCreate,
    ZoneDwellExceededCondition,
    ZoneEnteredCondition,
    ZoneExitedCondition,
)


def test_zone_entered_condition_defaults() -> None:
    c = ZoneEnteredCondition(zone_id="z1")
    assert c.cooldown_s == 60
    assert c.subject_kinds is None


def test_zone_exited_condition_explicit_kinds() -> None:
    c = ZoneExitedCondition(zone_id="z1", subject_kinds=["asset"])
    assert c.subject_kinds == ["asset"]


def test_zone_dwell_threshold_required() -> None:
    with pytest.raises(ValidationError):
        ZoneDwellExceededCondition(zone_id="z1", threshold_minutes=0)


def test_rule_create_accepts_zone_entered() -> None:
    rule = RuleCreate(
        name="enter alert",
        condition_type="zone.entered",
        condition_config={"zone_id": "z1", "cooldown_s": 30},
        action_type="webhook",
        action_config={"url": "http://x"},
    )
    assert rule.condition_type == "zone.entered"


def test_rule_create_accepts_zone_exited() -> None:
    rule = RuleCreate(
        name="exit alert",
        condition_type="zone.exited",
        condition_config={"zone_id": "z1"},
        action_type="webhook",
        action_config={"url": "http://x"},
    )
    assert rule.condition_type == "zone.exited"


def test_rule_create_accepts_zone_dwell() -> None:
    rule = RuleCreate(
        name="dwell alert",
        condition_type="zone.dwell_exceeded",
        condition_config={"zone_id": "z1", "threshold_minutes": 10},
        action_type="webhook",
        action_config={"url": "http://x"},
    )
    assert rule.condition_type == "zone.dwell_exceeded"


def test_rule_create_rejects_unknown_zone_condition() -> None:
    with pytest.raises(ValidationError):
        RuleCreate(
            name="bad",
            condition_type="zone.unknown",
            condition_config={},
            action_type="webhook",
            action_config={"url": "http://x"},
        )
