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


# -- Subject-scoped telemetry conditions (Sprint 20) --


class TelemetryThresholdCondition(BaseModel):
    """Threshold over a subject-scoped telemetry metric.

    Evaluated when a row lands in ``telemetry_readings`` matching the
    declared ``subject_kind`` + ``subject_id`` (or any subject of that kind
    when ``subject_id`` is omitted) + ``metric_name``. Pre-Sprint-20 rules
    used the generic ``threshold`` condition over a tag-read ``payload``
    field; this condition explicitly targets the persisted multi-subject
    telemetry stream introduced in Sprint 18.
    """

    subject_kind: str = Field(pattern=r"^(device|asset|lot|stock_item|zone)$")
    metric_name: str = Field(min_length=1)
    operator: str = Field(pattern=r"^(gt|lt|gte|lte|eq)$")
    value: float
    # Optional pin: alert only for this subject. Stored as UUID-as-string
    # for JSONB round-trip parity with the other inventory rule configs.
    subject_id: str | None = None
    cooldown_s: int = Field(default=300, ge=0)


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
    r"zone\.entered|zone\.exited|zone\.dwell_exceeded|"
    r"telemetry\.threshold|"
    # -- Sprint 41 / ADR-021 v2 Configurable Signaling Events --
    # The regex lists each valid (event_type, trigger) pair explicitly
    # so impossible combinations (e.g. ``signaling.temperature.on_entry``)
    # are rejected at parse time. Keep in sync with
    # ``SIGNALING_VALID_PAIRS`` below — the two are the same truth in
    # different shapes (one for Pydantic field validation, one for the
    # API / evaluator).
    r"signaling\.location\.(on_change|periodic|on_inactivity|on_inference)|"
    r"signaling\.geolocation\.(on_change|periodic|on_inactivity)|"
    r"signaling\.temperature\.(on_change|periodic|on_inactivity)|"
    r"signaling\.geofencing\.(on_entry|on_exit)"
    r")$"
)


# -- Signaling-event taxonomy (ADR-021 v2) --
#
# Authoritative map of which ``trigger`` values are legal for each
# ``event_type``. The regex above is generated from the same truth; if
# you add a pair, update both.
#
# - ``location`` / ``geolocation`` / ``temperature`` are subject-bound
#   metric streams; entry/exit are spatial primitives that don't apply.
# - ``geofencing`` is a pure spatial primitive; ``periodic`` /
#   ``on_inactivity`` / ``on_inference`` don't apply.
SIGNALING_VALID_PAIRS: dict[str, frozenset[str]] = {
    "location": frozenset({"on_change", "periodic", "on_inactivity", "on_inference"}),
    "geolocation": frozenset({"on_change", "periodic", "on_inactivity"}),
    "temperature": frozenset({"on_change", "periodic", "on_inactivity"}),
    "geofencing": frozenset({"on_entry", "on_exit"}),
}

# Two processor implementations per ADR-021. IsolatedZones is the
# pre-Sprint-41 implicit behaviour made explicit; OverlappingZones is
# new in Sprint 41 Phase D.
SIGNALING_PROCESSORS: tuple[str, ...] = ("isolated_zones", "overlapping_zones")


def split_signaling_condition_type(condition_type: str) -> tuple[str, str] | None:
    """Decompose ``signaling.<event_type>.<trigger>`` into its parts.

    Returns ``None`` for any string that is not a well-formed signaling
    condition_type (the caller can treat ``None`` as "legacy rule, no
    signaling-event taxonomy applies"). When a tuple is returned, the
    ``(event_type, trigger)`` pair is guaranteed to be one of the 12
    valid combinations in :data:`SIGNALING_VALID_PAIRS` — the
    :data:`_RULE_CONDITION_PATTERN` regex has already rejected
    ill-formed inputs before they reach this helper.
    """

    if not condition_type.startswith("signaling."):
        return None
    parts = condition_type.split(".")
    if len(parts) != 3:
        return None
    _, event_type, trigger = parts
    if trigger not in SIGNALING_VALID_PAIRS.get(event_type, frozenset()):
        return None
    return event_type, trigger


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
    condition_type: str | None = Field(default=None, pattern=_RULE_CONDITION_PATTERN)
    condition_config: dict[str, Any] | None = None
    action_type: str | None = Field(default=None, pattern=r"^(webhook|email|notification)$")
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
