"""Pydantic schemas for rules and alerts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# -- Condition types --


class ThresholdCondition(BaseModel):
    """Threshold breach: signal_strength > X or < X."""

    field: str = Field(min_length=1)
    operator: str = Field(pattern=r"^(gt|lt|gte|lte|eq)$")
    value: float


class AbsenceCondition(BaseModel):
    """Absence detection: tag not seen for N minutes."""

    tag_id: str | None = None
    minutes: int = Field(ge=1)


class RateChangeCondition(BaseModel):
    """Rate change: reads/min changed by more than X% over window."""

    window_minutes: int = Field(ge=1)
    change_percent: float = Field(gt=0)


# -- Rules --


class RuleCreate(BaseModel):
    """Create a new rule."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    condition_type: str = Field(pattern=r"^(threshold|absence|rate_change)$")
    condition_config: dict[str, Any]
    action_type: str = Field(pattern=r"^(webhook|email|notification)$")
    action_config: dict[str, Any]
    scope_device_id: UUID | None = None
    enabled: bool = True


class RuleUpdate(BaseModel):
    """Partial update for a rule."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    condition_type: str | None = Field(
        default=None, pattern=r"^(threshold|absence|rate_change)$"
    )
    condition_config: dict[str, Any] | None = None
    action_type: str | None = Field(
        default=None, pattern=r"^(webhook|email|notification)$"
    )
    action_config: dict[str, Any] | None = None
    scope_device_id: UUID | None = None
    enabled: bool | None = None


class RuleResponse(BaseModel):
    """Rule returned from the API."""

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    condition_type: str
    condition_config: dict[str, Any]
    action_type: str
    action_config: dict[str, Any]
    scope_device_id: UUID | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# -- Alerts --


class AlertResponse(BaseModel):
    """Alert returned from the API."""

    id: UUID
    tenant_id: UUID
    rule_id: UUID
    device_id: UUID | None
    severity: str
    message: str
    context: dict[str, Any]
    status: str
    triggered_at: datetime

    model_config = {"from_attributes": True}
