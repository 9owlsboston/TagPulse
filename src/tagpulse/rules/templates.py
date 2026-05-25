"""Built-in rule templates (Sprint 20).

Templates are pre-filled ``RuleCreate`` payloads the UI offers as
"new rule" starting points. Each template returns a fully valid
:class:`RuleCreate`-compatible dict; callers may edit any field
before POSTing to ``/rules``.

Templates are intentionally a flat list rather than a registry —
there are only a handful and adding one is a code change anyway.
"""

from __future__ import annotations

from typing import Any


class RuleTemplate:
    """A named, pre-filled rule recipe."""

    def __init__(
        self,
        *,
        key: str,
        name: str,
        description: str,
        condition_type: str,
        condition_config: dict[str, Any],
        action_type: str = "notification",
        action_config: dict[str, Any] | None = None,
        requires_subject_kind: str | None = None,
    ) -> None:
        self.key = key
        self.name = name
        self.description = description
        self.condition_type = condition_type
        self.condition_config = condition_config
        self.action_type = action_type
        self.action_config = action_config or {}
        # When set, the UI should only surface this template if the tenant
        # has at least one telemetry_models row of this subject_kind. The
        # backend does not enforce this — it is a discoverability hint.
        self.requires_subject_kind = requires_subject_kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "condition_type": self.condition_type,
            "condition_config": self.condition_config,
            "action_type": self.action_type,
            "action_config": self.action_config,
            "requires_subject_kind": self.requires_subject_kind,
        }


RULE_TEMPLATES: list[RuleTemplate] = [
    RuleTemplate(
        key="lot.cold_chain_breach",
        name="Cold-chain breach (lot)",
        description=(
            "Alert when a lot's temperature exceeds the configured "
            "threshold. Defaults: 8°C for refrigerated dairy, configurable "
            "in the UI before saving."
        ),
        condition_type="telemetry.threshold",
        condition_config={
            "subject_kind": "lot",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 8.0,
            "cooldown_s": 900,
        },
        requires_subject_kind="lot",
    ),
    RuleTemplate(
        key="asset.high_temperature",
        name="Asset over-temperature",
        description=(
            "Alert when an asset (e.g. a forklift battery) reports a temperature above threshold."
        ),
        condition_type="telemetry.threshold",
        condition_config={
            "subject_kind": "asset",
            "metric_name": "temperature_c",
            "operator": "gt",
            "value": 60.0,
            "cooldown_s": 600,
        },
        requires_subject_kind="asset",
    ),
]


def get_templates() -> list[RuleTemplate]:
    return list(RULE_TEMPLATES)


def get_template(key: str) -> RuleTemplate | None:
    for tpl in RULE_TEMPLATES:
        if tpl.key == key:
            return tpl
    return None
