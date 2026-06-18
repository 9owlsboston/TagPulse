"""SQLAlchemy ORM models for TagPulse database tables."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class TenantModel(Base):
    """Tenant table — organizations using the platform."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="standard")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    tracking_modes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default='["asset"]'
    )
    # -- Sprint 19: subject kinds the ingest pipeline is allowed to
    # fan-out telemetry to. ``["device"]`` (the default) preserves
    # Sprint 14 behaviour byte-for-byte; operators opt into
    # ``"asset"`` / ``"lot"`` / ``"stock_item"`` / ``"zone"`` once they
    # have a matching telemetry_models entry. See
    # docs/design/subject-scoped-telemetry.md §4. --
    telemetry_subject_kinds: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default='["device"]'
    )
    db_pool_key: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="shared_default"
    )
    provisioning_key_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provisioning_key_prefix: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # -- Sprint 17a: per-tenant tile-provider config (NULL = system default) --
    tile_provider: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # -- Sprint 22 A4: per-tenant rate-limit overrides (NULL = use globals).
    # Shape: {"ingest": int, "read": int, "write": int, "admin": int}.
    # Any subset of keys allowed; missing keys fall back to Settings. --
    rate_limit_overrides: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # -- Sprint 50 C1: per-tenant hourly cap on POST /tags/import calls.
    # Default 10 / hour per ADR 028 OQ 4. Lives in tagpulse.core.tag_import_rate_limit. --
    tag_bulk_import_rate_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="10"
    )
    # -- Sprint 50 C3: two-person-rule threshold per ADR 028 §Governance #4.
    # Bulk ops over this row count create a pending_bulk_operations row
    # that a second admin must approve. Default 10 000 matches the ADR. --
    tag_bulk_two_person_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="10000"
    )
    # -- Sprint 54 Phase 54.3: per-tenant threshold powering the
    # ``low_stock_count`` field on ``GET /dashboard/summary``. A product
    # is "low" when its count of active stock_items (``state='in_stock'
    # AND consumed_at IS NULL``) is strictly less than this value.
    # Default 3 per Sprint 54 planning; overridable via PATCH /tenant/config. --
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    # -- Sprint 54 follow-up: per-tenant tag-counting mode powering the
    # ``tags_total`` field on ``GET /dashboard/summary``. One of
    # ``"all"`` | ``"live"`` | ``"non_terminal"``. Default ``"live"``
    # matches the operator-facing Tags page's default filter. CHECK
    # constraint at the DB layer; Pydantic enforces the same enum on
    # PATCH /tenant/config. --
    dashboard_tags_count_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="live"
    )
    # -- Sprint 33 QW6: per-tenant branding (NULL = use system defaults).
    # See docs/design/reference-design-remediation.md §3.3.
    # Widened to Text (migration 054) so logo_url can hold a small base64
    # ``data:`` URL (uploaded logo), not just an ``https://`` URL.
    # ``logo_collapsed_url`` is the second logo shown on the collapsed sidebar. --
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_collapsed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # -- Sprint 59 Track 2 (59.9, ADR-024 D8): per-tenant indoor-position
    # estimator config placeholder. NULL = unconfigured. Created-not-used in
    # Sprint 59 — the RSSI/count weight formula varies company-to-company, so
    # it must be config, never hardcoded; the Sprint 61 estimator reads it. --
    position_strategy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # -- Sprint 60 increment 3 (ADR-032 §3): per-tenant Configurable-UI
    # presentation defaults. NULL = pure system default. Tenant-default leaves
    # live at the top level; the role layer is keyed under a reserved ``roles``
    # sub-object (``{"theme": {...}, "roles": {"viewer": {...}}}``). Reuses the
    # tenant-JSONB precedent above — resolution onto role/user/system happens in
    # tagpulse.services.ui_config, never here. --
    ui_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeviceModel(Base):
    """Device registry table — stores reader registrations and metadata."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False, default="rfid_reader")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    mobility: Mapped[str] = mapped_column(String(16), nullable=False, server_default="fixed")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # Sprint 64 follow-up: the site/floor a (fixed) reader physically lives on.
    # NULL for mobile/un-assigned readers. Enables floor-polygon zone resolution.
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    connection_state: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # -- Sprint 16: rotatable per-device token (ADR-011 Phase 1) --
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_prefix: Mapped[str | None] = mapped_column(String(10), nullable=True)
    token_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -- Sprint 17b: mTLS for MQTT (ADR-012 Phase 2) --
    cert_thumbprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cert_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TagReadModel(Base):
    """Tag read events hypertable — time-series RFID tag read data."""

    __tablename__ = "tag_reads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    tag_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    signal_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    sensor_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # -- Sprint 14: location --
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # -- Sprint 14: RFID identity --
    epc: Mapped[str | None] = mapped_column(String(256), nullable=True)
    epc_hex: Mapped[str | None] = mapped_column(String(128), nullable=True)
    epc_scheme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    epc_decoded: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_memory_hex: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reader_antenna: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # -- Sprint 50 Phase D (ADR 028 §"Gating"): three-valued tag registry
    # gate. NULL on insert (ingest never reads ``tags``); flipped to TRUE
    # / FALSE by :class:`TagRegistrarWorker`. No index by design — the
    # worker drains a small NULL backlog from the recent hypertable
    # chunks. See ``migrations/versions/044_tag_reads_tag_known.py``.
    tag_known: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TelemetryReadingModel(Base):
    """Subject-scoped telemetry hypertable (Sprint 18).

    Authoritative store. Keyed on ``(tenant_id, subject_kind, subject_id)``
    so a single reading can be attributed to the device that reported it,
    the asset bound to the tag it scanned, the lot/stock-item the tag
    decodes to, or the zone it currently sits in. See
    :doc:`docs/design/subject-scoped-telemetry` for the full data model
    and ADR-013 for the rename-not-drop migration strategy.
    """

    __tablename__ = "telemetry_readings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="device")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)


class TelemetryQuarantineModel(Base):
    """Quarantine for unknown / out-of-range telemetry readings (Sprint 14).

    Sprint 18 added nullable ``subject_kind`` / ``subject_id`` columns so
    multi-subject ingest can record the resolved subject when the reading
    fails validation. Legacy rows (back-filled from ``device_telemetry``)
    leave both columns NULL.
    """

    __tablename__ = "telemetry_quarantine"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class TelemetryModelDef(Base):
    """Telemetry model definitions — per-subject metric schemas.

    Sprint 18 added the ``subject_kind`` column. ``device_type`` is now
    only required when ``subject_kind='device'`` (the original Sprint 14
    case); for ``asset`` / ``lot`` / ``stock_item`` / ``zone`` it must be
    ``NULL`` and the model is shared across all subjects of that kind for
    the tenant. Uniqueness is enforced by
    ``ix_telemetry_models_tenant_subject`` on
    ``(tenant_id, subject_kind, COALESCE(device_type, ''))``.
    """

    __tablename__ = "telemetry_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="device")
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TenantUsageDetail(Base):
    """Daily per-dimension usage counters per tenant."""

    __tablename__ = "tenant_usage_detail"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    usage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(50), primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)


class TenantQuota(Base):
    """Per-dimension quotas per tenant."""

    __tablename__ = "tenant_quotas"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    dimension: Mapped[str] = mapped_column(String(50), primary_key=True)
    max_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    action_on_exceed: Mapped[str] = mapped_column(String(20), nullable=False, default="throttle")


class RuleModel(Base):
    """User-defined rules evaluated against incoming telemetry."""

    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition_type: Mapped[str] = mapped_column(String(50), nullable=False)
    condition_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scope_device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # -- Sprint 41 / ADR-021 v2 Configurable Signaling Events --
    # ``event_type IS NULL`` is the legacy-rule discriminator; signaling
    # rules populate all three of (event_type, trigger, processor).
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger: Mapped[str | None] = mapped_column(String(32), nullable=True)
    processor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence_threshold: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default="0.0"
    )
    category_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    )
    asset_label_filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    zone_label_filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    site_label_filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    integration_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AlertModel(Base):
    """Triggered alert history — time-series."""

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rules.id"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AnalyticsResultModel(Base):
    """Analytics module computed results."""

    __tablename__ = "analytics_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    module_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class IntegrationModel(Base):
    """Integration targets — webhook, SSE, export configurations."""

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    health_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    enrichments: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    last_triggered: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class IntegrationDeliveryModel(Base):
    """Delivery log for integration targets."""

    __tablename__ = "integration_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_code: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class DeadLetterEventModel(Base):
    """Dead-lettered events that failed processing."""

    __tablename__ = "dead_letter_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    topic: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # Sprint 28 C3 — low-cardinality source classifier so the triage
    # runbook can route rows to the right investigator without parsing
    # ``topic``. Values constrained by ``ck_dead_letter_events_source``:
    # event_bus | tag_read_rejected | mqtt_subscriber | other.
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="event_bus", server_default="event_bus"
    )
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class AuditLogModel(Base):
    """Audit trail for configuration changes."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Sprint 50 Phase C5 — unified bulk-op shape per ADR 028 §Governance #7.
    # All five columns are NULL for non-bulk-op rows (device tokens,
    # label changes, etc.). See migration 048.
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    batch: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pending_bulk_operations.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class UserModel(Base):
    """Users within a tenant — individual identity with role-based access."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    api_key_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(10), nullable=True)
    api_key_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserUiPrefsModel(Base):
    """Per-user UI presentation overrides (Sprint 60, ADR-032 §3 user layer).

    Sibling of :class:`UserModel` (``user_id`` PK grain), so — like ``users`` —
    it carries **no RLS**: the request path scopes by the globally-unique
    ``user_id`` PK, not the ``app.current_tenant_id`` GUC. ``prefs`` is the
    **sparse** per-leaf override (a subset of the ADR-032 §4 document); missing
    keys fall through to role/tenant/system at resolve time. "Reset to team
    default" = delete the row (``ON DELETE CASCADE`` from ``users``).
    """

    __tablename__ = "user_ui_prefs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SiteModel(Base):
    """Physical location grouping (Sprint 15) — building/yard/warehouse.

    Sprint 34 (gap 2.7) adds ``kind`` (``site`` | ``transporter``),
    ``latitude`` / ``longitude`` (paired, range-CHECKed), and a
    structured-address breakout. The legacy free-form ``address``
    column is retained this release as a compatibility shadow; the
    application layer is free to populate it from the structured
    fields.
    """

    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="site")
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # -- Sprint 34 gap 2.7: structured address --
    street_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # CHAR(2) matches migration 038; regex CHECK enforces exact 2-char shape
    # so no padding ever occurs.
    country: Mapped[str | None] = mapped_column(CHAR(2), nullable=True)
    # -- Sprint 34 gap 2.7: geolocation --
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # -- Sprint 59 Track 2 (59.9): floor coordinate frame for indoor (x, y).
    # NULL = geographic-only (today's behaviour). Shape (units, extent, origin
    # anchor, rotation, optional geo-anchor) per ADR-024. --
    coord_system: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    default_timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ZoneModel(Base):
    """Logical area within a site (Sprint 15) — reader-bound; geofence in S17a."""

    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    fixed_reader_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    polygon_geojson: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # -- Sprint 17a: denormalized bbox for the geofence prefilter --
    bbox_min_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_min_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SubjectCurrentZoneModel(Base):
    """Latest known zone per subject (Sprint 17a §5.2).

    Persistence backing for ``DwellTracker``. One row per
    ``(tenant_id, subject_kind, subject_id)``; upserted by the ingestion path
    on every ``subject.zone_changed`` event so the dwell worker survives
    restart and works in multi-worker deployments.
    """

    __tablename__ = "subject_current_zone"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    zone_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TagPresenceModel(Base):
    """Current-state EPC presence per (tenant, device) (Sprint 46, ADR-026 §3.1).

    Regular table — NOT a hypertable. Row count is bounded by EPC fleet
    size, every update touches the current row, so the partitioned
    layout would only add overhead. Maintained synchronously by the
    presence reconciler on v2 wire messages (see
    :mod:`tagpulse.ingestion.presence_reconciler`). Schema lives in
    migration ``042_tag_presence.py``.

    ``status`` is a free-form ``VARCHAR(16)`` with a DB ``CHECK`` to
    ``'present' | 'gone'``. Two partial indexes (both
    ``WHERE status='present'``) cover the "what's at this reader now"
    and "where is this EPC now" queries. RLS is enabled via the
    ``app.current_tenant_id`` session GUC.
    """

    __tablename__ = "tag_presence"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    epc: Mapped[str] = mapped_column(String(124), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_rssi: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_antenna: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class CategoryModel(Base):
    """Tenant-scoped Category for assets (Sprint 34, ADR 019).

    Every asset *should* belong to exactly one Category. Category
    declares the signaling-event capability template (``category_type``)
    and the required-tag count consumed by ADR 021 (Configurable
    Signaling Events). (A separate tag-registry entity is deferred —
    TagPulse already has equivalents via ``tag_reads`` +
    ``asset_tag_bindings``; see ``docs/data-models.md`` §"Where is the
    tag?".)

    Invariants:

    - ``UNIQUE(tenant_id, name)`` — enforced by the DB.
    - ``category_type`` must be one of ``liquid_container`` /
      ``reference_tag`` / ``rti_container`` / ``object`` — enforced
      by a DB ``CHECK`` constraint AND a Pydantic ``Literal``.
    - ``category_type`` is **immutable after create** — enforced in
      the API layer (``PATCH`` rejects changes), not in the DB.
    - ``required_tags`` must be ``>= 1`` — DB ``CHECK``.
    - Cannot be deleted while any asset references it — enforced by
      the ``ON DELETE RESTRICT`` FK on ``assets.category_id``; the API
      surfaces a 409 with the count of referencing assets.
    """

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku_upc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required_tags: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_categories_tenant_name"),)


class LabelModel(Base):
    """Tenant-scoped label catalog row (Sprint 35, ADR 020).

    A label is a *(key)* slot scoped to one ``entity_type``. The same
    key can mean different things on different entity kinds — e.g.
    ``location`` on a Site is a physical address; ``location`` on an
    Asset is "which Site is it at right now". That's why the
    catalog's uniqueness is ``(tenant_id, entity_type, lower(key))``
    (functional unique index — see migration 039).

    Invariants:

    - ``entity_type`` must be one of ``asset`` / ``site`` / ``zone``
      / ``device`` / ``category`` — DB ``CHECK`` constraint.
    - ``key`` matches ``^[A-Za-z0-9_.+$]{3,24}$`` — DB ``CHECK``.
    - ``color`` is optional but, if set, matches ``^#[0-9A-Fa-f]{6}$``
      — DB ``CHECK``.
    - Cannot be deleted while any ``entity_labels`` row references
      it — enforced by the ``ON DELETE RESTRICT`` FK on
      ``entity_labels.label_id``; the API surfaces a 409 with the
      association count.
    - ``created_by`` / ``updated_by`` are opaque JWT user ids (no FK,
      same as ``audit_logs.user_id``).
    """

    __tablename__ = "labels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(24), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # NOTE: case-insensitive UNIQUE(tenant_id, entity_type, lower(key))
    # is a functional index created in migration 039 — it cannot be
    # expressed via UniqueConstraint() and so is intentionally absent
    # from __table_args__. ORM-level uniqueness checks should use the
    # repository's ``find_by_key()`` helper, not assume the column
    # constraint will catch it.


class EntityLabelModel(Base):
    """Polymorphic label-to-entity association (Sprint 35, ADR 020).

    ``entity_id`` has **no FK** — it points at one of ``assets`` /
    ``sites`` / ``zones`` / ``devices`` / ``categories`` depending on
    the parent label's ``entity_type``. Orphan rows are cleaned up
    by the entity-delete handlers in their respective routers, not
    by a database CASCADE.

    Invariants:

    - Composite primary key ``(label_id, entity_id)`` prevents
      double-association of the same label to the same entity.
    - ``value`` matches ``^[A-Za-z0-9._-]{1,64}$`` — DB ``CHECK``.
    - 30-per-entity cap enforced by the ``trg_enforce_label_cap``
      BEFORE INSERT trigger; the API layer also early-rejects on the
      31st insert. SQLSTATE ``23514`` surfaces as a 409 in the API.
    """

    __tablename__ = "entity_labels"

    label_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, index=True)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssetModel(Base):
    """Tenant-scoped tracked thing (Sprint 15)."""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # -- Sprint 34 (ADR 019) introduced this FK as nullable alongside
    # the legacy ``asset_type`` shadow column. Sprint 41 Phase H
    # (migration 041) dropped the shadow and promoted this column to
    # ``NOT NULL`` \u2014 every asset must point at a Category. --
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AssetTagBindingModel(Base):
    """Historical tag-to-asset binding (Sprint 15)."""

    __tablename__ = "asset_tag_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    binding_value: Mapped[str] = mapped_column(String(256), nullable=False)
    binding_kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="epc")
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)


class ExternalLocationModel(Base):
    """Non-RFID position fix for an asset (Sprint 15 Phase C)."""

    __tablename__ = "external_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    accuracy_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)


class AntennaModel(Base):
    """Per-antenna position within a site's coordinate frame (Sprint 59 Track 2).

    Position lives per **antenna**, not per device: a fixed positioning reader
    fans 2-8 antennas across tens of metres of coax, each a distinct radiator at
    a distinct ``(x, y)``. ``port`` matches ``tag_reads.reader_antenna``.
    Tenant isolation flows through the ``device_id`` FK (devices are
    tenant-scoped), so this table carries no ``tenant_id`` of its own. Amends
    ADR-024 (v1 put ``position_*`` on ``devices``).
    """

    __tablename__ = "antennas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    port: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    x: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    y: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    z: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gain_dbi: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    __table_args__ = (UniqueConstraint("device_id", "port", name="uq_antennas_device_port"),)


class AssetPositionModel(Base):
    """Per-asset ``(x, y)`` position fix hypertable (Sprint 59 Track 2).

    Created in Sprint 59 but written to by nothing: ``source='precomputed'`` is
    the Sprint 60 BYO-ingest path, ``'computed'`` is the Sprint 61 estimator,
    and ``'zone'`` is the Sprint 60 retrieval-time fallback. ``asset_id`` carries
    no FK (hypertable, matches ADR-013/014). The ``id + time`` composite PK
    follows the ``external_locations`` precedent.
    """

    __tablename__ = "asset_positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    x: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    y: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    z: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)


# ============================================================================
# Sprint 15b — Inventory tracking
# ============================================================================


class ProductModel(Base):
    """SKU catalog row (Sprint 15b)."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    gtin: Mapped[str | None] = mapped_column(String(14), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, server_default="each")
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LotModel(Base):
    """Production batch / expiry (Sprint 15b)."""

    __tablename__ = "lots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    lot_code: Mapped[str] = mapped_column(String(64), nullable=False)
    manufactured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StockItemModel(Base):
    """Per-tag inventory unit (Sprint 15b)."""

    __tablename__ = "stock_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lots.id"), nullable=True
    )
    parent_stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stock_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    binding_value: Mapped[str] = mapped_column(String(256), nullable=False)
    binding_kind: Mapped[str] = mapped_column(String(8), nullable=False, server_default="epc")
    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="in_stock")
    current_zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)


class StockMovementModel(Base):
    """Append-only inventory movement ledger (Sprint 15b)."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    stock_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_zone_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )


class TagDataMappingModel(Base):
    """Per-(tenant, scope) mapping tag_data key -> semantic field (Sprint 15b)."""

    __tablename__ = "tag_data_mappings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    semantic_field: Mapped[str] = mapped_column(String(40), nullable=False)
    tag_data_key: Mapped[str] = mapped_column(String(64), nullable=False)
    transform: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TagModel(Base):
    """Tenant-scoped EPC identity/ownership row (Sprint 50, ADR 028).

    One row per ``(tenant_id, epc_hex)`` — that pair is the natural
    key (see ``uq_tags_tenant_epc`` in migration 043). ``epc_hex`` is
    the canonical uppercase-hex form with no separators; the CHECK
    constraint ``ck_tags_epc_hex_format`` enforces ``^[0-9A-F]{16,128}$``.

    ``gs1_uri`` is a *denormalized* lenient parse of the GS1
    identifier (e.g. ``urn:epc:id:sgtin:0614141.012345.62852``) for
    EPCs whose header maps to a known scheme; ``NULL`` for raw /
    proprietary / unparseable tags. The partial index
    ``ix_tags_tenant_gs1_uri`` covers the populated subset.

    ``status`` ∈ ``{registered, active, retired, defective,
    transferred_out}``; ``source`` ∈ ``{csv_import, api, backfill,
    transfer_in}`` (no ``first_read`` per ADR 028 OQ 3). Both are
    ``VARCHAR(16)`` with CHECK constraints rather than native enums
    so additive evolution stays cheap. Status transition rules live
    in the service layer (``tagpulse.services.tags``).

    ``first_seen_at`` / ``last_seen_at`` are populated by the
    registrar worker (Phase D, not yet built) — Phase B leaves them
    ``NULL`` on create.
    """

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    epc_hex: Mapped[str] = mapped_column(String(128), nullable=False)
    gs1_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("tenant_id", "epc_hex", name="uq_tags_tenant_epc"),)


class TagTransferModel(Base):
    """Cross-tenant transfer audit log row (Sprint 50, ADR 028).

    One row per EPC; all rows belonging to one operator-initiated
    request share a ``request_id``. The schema deliberately omits a
    ``tenant_id`` column — the row already names both sides
    (``from_tenant_id``, ``to_tenant_id``) and the RLS policy
    ``tenant_isolation_tag_transfers`` lets either side see it.

    ``status`` ∈ ``{requested, completed, failed}`` with cross-column
    invariants (see ``ck_tag_transfers_terminal_failure_reason`` and
    ``ck_tag_transfers_completed_at`` in migration 043). Phase B
    creates rows in ``requested`` only; the completion / failure
    path lands with the receiving-tenant acknowledgement flow.
    """

    __tablename__ = "tag_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    to_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    epc_hex: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PendingBulkOperationModel(Base):
    """Pending two-person-rule bulk op (Sprint 50 C3, ADR 028 §Governance #4).

    One row per operator-initiated bulk op whose row count meets or
    exceeds the tenant's ``tag_bulk_two_person_threshold``. The row
    stashes the raw CSV bytes (``payload``) plus the C2
    ``content_hash`` so the approve path can verify the stored
    payload still matches what the first admin previewed before
    executing.

    The table is generic: the ``operation`` column discriminates
    (currently only ``tags.import``; C4 will add
    ``tags.bulk_patch`` / ``tags.bulk_retire``). The approve
    endpoint dispatches by ``operation`` to the appropriate
    executor; one table covers every bulk endpoint.

    State machine: ``pending -> approved -> executed`` is the happy
    path (``executed`` is set after the actual bulk op succeeds);
    ``pending -> rejected`` is the deny path; ``pending -> expired``
    is the timeout path swept lazily on ``approve``.
    """

    __tablename__ = "pending_bulk_operations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sample: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
