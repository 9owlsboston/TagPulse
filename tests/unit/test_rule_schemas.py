"""Unit tests for rule schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.rule_schemas import RuleCreate, RuleUpdate


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
