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


# -- Inventory conditions (Sprint 15b Phase E) --


class StockBelowThresholdCondition(BaseModel):
    """Periodic scan: alert when (product[, lot][, zone]) stock falls below N."""

    product_id: str  # UUID-as-string for JSONB round-trip parity
    lot_id: str | None = None
    zone_id: str | None = None
    threshold: int = Field(ge=0)


class StockExpiringWithinCondition(BaseModel):
    """Periodic scan: alert when any lot for product expires within N days."""

    product_id: str | None = None  # None = all products in tenant
    days: int = Field(ge=0)


class StockUnexpectedInZoneCondition(BaseModel):
    """Event-driven: alert on stock_item entering zone NOT in allowed list."""

    product_id: str | None = None  # None = applies to all products
    allowed_zone_ids: list[str] = Field(min_length=1)


# -- Geofence conditions (Sprint 17a) --


class ZoneEnteredCondition(BaseModel):
    """Event-driven: alert when subject enters the named zone.

    Applies to ``subject.zone_changed`` events with matching ``to_zone_id``.
    ``cooldown_s`` suppresses repeat-alerts for the same (rule, subject) pair
    within the window, defending against flapping reads.
    """

    zone_id: str
    subject_kinds: list[str] | None = None  # None = any (asset, stock_item, device)
    cooldown_s: int = Field(default=60, ge=0)


class ZoneExitedCondition(BaseModel):
    """Event-driven: alert when subject leaves the named zone."""

    zone_id: str
    subject_kinds: list[str] | None = None
    cooldown_s: int = Field(default=60, ge=0)


class ZoneDwellExceededCondition(BaseModel):
    """Periodic: alert when subject dwells in zone longer than threshold."""

    zone_id: str
    threshold_minutes: int = Field(ge=1)
    subject_kinds: list[str] | None = None
    cooldown_s: int = Field(default=300, ge=0)


_RULE_CONDITION_PATTERN = (
    r"^(threshold|absence|rate_change|"
    r"stock\.below_threshold|stock\.expiring_within|stock\.unexpected_in_zone|"
    r"zone\.entered|zone\.exited|zone\.dwell_exceeded)$"
)


# -- Rules --


class RuleCreate(BaseModel):
    """Create a new rule."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    condition_type: str = Field(pattern=_RULE_CONDITION_PATTERN)
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
        default=None, pattern=_RULE_CONDITION_PATTERN
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
