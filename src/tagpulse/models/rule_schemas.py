"""Pydantic schemas for rules and alerts."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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


# -- Per-trigger condition_config schemas (Sprint 41 Phase B, ADR-021 v2) --
#
# Each schema captures the *shape* of ``rules.condition_config`` for one
# signaling trigger. The two-level discriminated union per ADR open
# question #2 (event_type → trigger) keeps per-branch schemas small and
# error messages targeted. Validation is dispatched through
# :func:`validate_signaling_condition_config` from the service layer
# rather than declared as a Pydantic Field union on ``RuleCreate``
# because ``condition_config`` is a free-form ``dict[str, Any]`` at the
# column level (JSONB) and many legacy condition_types use
# bespoke shapes that don't fit the signaling discriminant.


class SignalingPeriodicConfig(BaseModel):
    """``signaling.<event_type>.periodic`` — cadence-driven evaluation.

    Cadence is bounded 1–1440 minutes (= 1 minute … 1 day). The
    :class:`tagpulse.signaling.periodic_dispatcher.PeriodicSignalingDispatcher`
    wakes on its loop tick, finds rules whose ``cadence_minutes`` has
    elapsed since the rule's last evaluation, and invokes the
    event-type-specific evaluator hook (Phase D).
    """

    cadence_minutes: int = Field(ge=1, le=1440)


class SignalingOnChangeConfig(BaseModel):
    """``signaling.<event_type>.on_change`` — fires on value transitions.

    For ``location`` / ``geolocation``: a zone-change event. For
    ``temperature``: a min-delta breach (``min_delta`` is the absolute
    change required to fire, in the metric's native unit — °C, kPa…).
    Cooldown defends against flapping reads.
    """

    min_delta: float = Field(default=0.0, ge=0.0)
    cooldown_s: int = Field(default=60, ge=0)


class SignalingOnInactivityConfig(BaseModel):
    """``signaling.<event_type>.on_inactivity`` — fires after silence.

    ``inactivity_minutes`` is the gap (since the subject's last
    observation of this event_type) that must elapse before the rule
    fires. Bounded to a day; longer absences should use a dedicated
    inventory rule.
    """

    inactivity_minutes: int = Field(ge=1, le=1440)
    cooldown_s: int = Field(default=300, ge=0)


class SignalingOnInferenceConfig(BaseModel):
    """``signaling.location.on_inference`` — fires on attribution-settled.

    Subscribes (via the rules engine) to
    :attr:`tagpulse.events.protocol.Topic.SIGNALING_ATTRIBUTION_SETTLED`
    events emitted by the OverlappingZones processor (Phase D).
    ``min_confidence`` filters out low-confidence attributions; defaults
    to 0.0 (= accept any settled attribution).
    """

    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cooldown_s: int = Field(default=60, ge=0)


class SignalingOnEntryConfig(BaseModel):
    """``signaling.geofencing.on_entry`` — spatial entry event.

    Same shape as the legacy :class:`ZoneEnteredCondition` so the
    Phase D evaluator can reuse the zone-change subscription. Distinct
    condition_type so the new signaling envelope (Phase C) fires for it
    and the cap-enforcement scope (Phase B2) treats it as a
    ``geofencing`` rule.
    """

    zone_id: str
    subject_kinds: list[str] | None = None
    cooldown_s: int = Field(default=60, ge=0)


class SignalingOnExitConfig(BaseModel):
    """``signaling.geofencing.on_exit`` — spatial exit event.

    Mirror of :class:`SignalingOnEntryConfig` for exits.
    """

    zone_id: str
    subject_kinds: list[str] | None = None
    cooldown_s: int = Field(default=60, ge=0)


class SignalingOverlappingZonesProcessorConfig(BaseModel):
    """Optional ``processor_config`` sub-block when ``processor='overlapping_zones'``.

    Per ADR-021 §"Processor implementation" + open question #5. The
    aggregation window is an enum (not arbitrary seconds) so operators
    pick from validated, dispatcher-supported values rather than typing
    a sub-second tick that overloads the worker.
    """

    aggregation_window_s: Literal[30, 60, 300, 1800] = 60
    min_rssi_dbm: float = Field(default=-80.0, le=0.0)
    zone_bleed_filter: bool = True
    aging_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    time_error_filter: int = Field(default=5, ge=0, le=60)


# Trigger → config-model lookup used by
# :func:`validate_signaling_condition_config`. Keep in sync with the
# trigger enum in :data:`SIGNALING_VALID_PAIRS`.
_SIGNALING_TRIGGER_CONFIGS: dict[str, type[BaseModel]] = {
    "periodic": SignalingPeriodicConfig,
    "on_change": SignalingOnChangeConfig,
    "on_inactivity": SignalingOnInactivityConfig,
    "on_inference": SignalingOnInferenceConfig,
    "on_entry": SignalingOnEntryConfig,
    "on_exit": SignalingOnExitConfig,
}


def validate_signaling_condition_config(
    condition_type: str, condition_config: dict[str, Any]
) -> BaseModel | None:
    """Validate a signaling rule's ``condition_config`` against its trigger.

    Returns the parsed config model on success, or ``None`` if the
    condition_type is not a signaling rule (caller treats this as a
    legacy rule and skips signaling-specific validation). Raises
    :class:`pydantic.ValidationError` if the trigger's schema rejects
    the payload. Service-layer callers should propagate validation
    errors so the API surfaces them as HTTP 422 with the standard
    Pydantic shape.
    """

    parts = split_signaling_condition_type(condition_type)
    if parts is None:
        return None
    _, trigger = parts
    schema_cls = _SIGNALING_TRIGGER_CONFIGS.get(trigger)
    if schema_cls is None:  # pragma: no cover — guarded by split_*
        return None
    return schema_cls.model_validate(condition_config)


# Default cap per ADR-021 open question #4 / Sprint 41 Phase B2: at most
# 5 active signaling rules per ``(tenant_id, event_type, category_id)``
# scope. Admin-only ``?override=true`` flag bypasses with an audit-log
# entry. Enforced in the API + service layer (not via a DB constraint)
# so error messages are friendly and per-tenant relaxation is cheap.
SIGNALING_DEFAULT_CAP_PER_SCOPE: int = 5


# -- Rules --


# Signaling-rule scoping fields shared by Create + Update + Response.
# Defaults match the migration 040 column server defaults so an unset
# field round-trips identically through a legacy-rule create.
_SIGNALING_PROCESSOR_PATTERN = r"^(isolated_zones|overlapping_zones)$"
_SIGNALING_EVENT_TYPE_PATTERN = r"^(location|geolocation|temperature|geofencing)$"
_SIGNALING_TRIGGER_PATTERN = r"^(on_change|periodic|on_inactivity|on_inference|on_entry|on_exit)$"


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
    # -- Sprint 41 / ADR-021 v2 Configurable Signaling Events --
    # Populated for signaling rules; all NULL for legacy rules (the
    # implicit ``event_type IS NULL`` discriminator at the column
    # level). Per ADR open question #2 the trigger → ``condition_config``
    # shape is validated by :func:`validate_signaling_condition_config`
    # in the service layer rather than via a giant discriminated union
    # on this model, so legacy rules' free-form configs stay untouched.
    event_type: str | None = Field(default=None, pattern=_SIGNALING_EVENT_TYPE_PATTERN)
    trigger: str | None = Field(default=None, pattern=_SIGNALING_TRIGGER_PATTERN)
    processor: str | None = Field(default=None, pattern=_SIGNALING_PROCESSOR_PATTERN)
    confidence_threshold: Decimal = Field(default=Decimal("0.0"), ge=0, le=1)
    category_ids: list[UUID] = Field(default_factory=list)
    asset_label_filters: list[dict[str, Any]] | None = None
    zone_label_filters: list[dict[str, Any]] | None = None
    site_label_filters: list[dict[str, Any]] | None = None
    integration_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def _signaling_columns_match_condition_type(self) -> "RuleCreate":
        """Cross-field guard: signaling condition_types must populate
        ``event_type`` + ``trigger`` consistent with the condition_type
        string; legacy condition_types must leave both NULL. Prevents
        the cap-enforcement scope (Phase B2) from being silently
        bypassed by sending ``condition_type='signaling.location.periodic'``
        with ``event_type=None``.
        """

        parts = split_signaling_condition_type(self.condition_type)
        if parts is None:
            if self.event_type is not None or self.trigger is not None:
                raise ValueError("event_type/trigger may only be set on signaling rules")
            return self
        expected_event_type, expected_trigger = parts
        if self.event_type is None:
            self.event_type = expected_event_type
        elif self.event_type != expected_event_type:
            raise ValueError(
                f"event_type={self.event_type!r} does not match condition_type "
                f"{self.condition_type!r}"
            )
        if self.trigger is None:
            self.trigger = expected_trigger
        elif self.trigger != expected_trigger:
            raise ValueError(
                f"trigger={self.trigger!r} does not match condition_type {self.condition_type!r}"
            )
        return self


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
    # Sprint 41: signaling scoping is patchable but the
    # condition_type / event_type / trigger consistency check happens in
    # the service layer (which knows the prior row's values).
    event_type: str | None = Field(default=None, pattern=_SIGNALING_EVENT_TYPE_PATTERN)
    trigger: str | None = Field(default=None, pattern=_SIGNALING_TRIGGER_PATTERN)
    processor: str | None = Field(default=None, pattern=_SIGNALING_PROCESSOR_PATTERN)
    confidence_threshold: Decimal | None = Field(default=None, ge=0, le=1)
    category_ids: list[UUID] | None = None
    asset_label_filters: list[dict[str, Any]] | None = None
    zone_label_filters: list[dict[str, Any]] | None = None
    site_label_filters: list[dict[str, Any]] | None = None
    integration_ids: list[UUID] | None = None


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
    # Sprint 41 / ADR-021 v2 signaling-event fields.
    event_type: str | None = None
    trigger: str | None = None
    processor: str | None = None
    confidence_threshold: Decimal = Decimal("0.0")
    category_ids: list[UUID] = Field(default_factory=list)
    asset_label_filters: list[dict[str, Any]] | None = None
    zone_label_filters: list[dict[str, Any]] | None = None
    site_label_filters: list[dict[str, Any]] | None = None
    integration_ids: list[UUID] | None = None
    # Computed discriminator surfaced for UI / clients per ADR-021
    # Consequences §"trade-offs". ``event_type IS NULL`` → ``legacy``;
    # otherwise ``signaling``. The column-level discriminator stays
    # implicit; this property makes it explicit at the API layer.
    kind: Literal["legacy", "signaling"] = "legacy"

    @model_validator(mode="after")
    def _populate_kind(self) -> "RuleResponse":
        self.kind = "signaling" if self.event_type is not None else "legacy"
        return self

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
