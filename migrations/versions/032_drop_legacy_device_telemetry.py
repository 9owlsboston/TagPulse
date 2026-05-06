"""Sprint 21: drop the Sprint 18 back-compat telemetry artifacts.

Revision ID: 032
Revises: 031
Create Date: 2026-05-05

Closes the deprecation window opened in Sprint 18 (ADR-013 §3, ADR-015 §6):

1. Drop the ``device_telemetry`` SQL view that re-exposed
   ``telemetry_readings WHERE subject_kind='device'`` for Sprint 14
   consumers (Grafana, ad-hoc psql).
2. Drop the ``telemetry_readings_legacy_device`` hypertable that held
   the pre-Sprint-18 device-scoped rows. By the time this migration
   runs, every active row has long since been mirrored into
   ``telemetry_readings`` (the Sprint 18 back-fill) and the slowest
   tenant's ``telemetry_retention_days`` window has cycled past their
   first non-``device`` opt-in (the ADR-015 §6 trigger).

Pre-flight checks (operator runs before applying — see
``docs/runbooks/subject-scoped-telemetry.md``):

* ``pg_stat_user_tables`` shows zero ``seq_scan`` / ``idx_scan`` on
  ``telemetry_readings_legacy_device`` for at least one full retention
  window.
* ``grep -R 'device_telemetry\\|telemetry_readings_legacy_device' src/``
  returns nothing (the Sprint 21 code drop removes the deprecated
  ``TimescaleTelemetryRepository`` and ``DeviceTelemetryModel``).
* No Grafana dashboard references the ``device_telemetry`` view.

``downgrade()`` re-creates **empty** structures with the original
shape so the round-trip migration check still passes; row data is
**not** restored. This is documented as an irreversible migration in
the runbook because the legacy back-fill source is itself derived
from ``telemetry_readings`` and the back-compat view was read-only.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) View first — it depends on the hypertable's column shape only
    # transitively (the view is over telemetry_readings, not the legacy
    # table), but dropping it first keeps the clean-up order consistent
    # with the Sprint 18 reverse-LIFO style.
    op.execute("DROP VIEW IF EXISTS device_telemetry")

    # 2) Indexes / policies attached to the legacy hypertable. Names
    # were inherited from migration 016 (cosmetic — they still
    # reference 'device_telemetry' even after the Sprint 18 rename).
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_device_telemetry "
        "ON telemetry_readings_legacy_device"
    )
    op.execute(
        "ALTER TABLE IF EXISTS telemetry_readings_legacy_device "
        "DISABLE ROW LEVEL SECURITY"
    )
    op.execute("DROP INDEX IF EXISTS ix_device_telemetry_lookup")

    # 3) Drop the legacy hypertable. TimescaleDB's drop_hypertable is
    # implicit on DROP TABLE.
    op.execute("DROP TABLE IF EXISTS telemetry_readings_legacy_device")


def downgrade() -> None:
    # Re-create empty structures so ``alembic downgrade`` succeeds; row
    # data is intentionally not restored (see module docstring).
    op.create_table(
        "telemetry_readings_legacy_device",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    op.execute(
        "SELECT create_hypertable('telemetry_readings_legacy_device', "
        "'timestamp', if_not_exists => TRUE)"
    )
    op.execute(
        "CREATE INDEX ix_device_telemetry_lookup "
        "ON telemetry_readings_legacy_device "
        "(tenant_id, device_id, metric_name, timestamp DESC)"
    )
    op.execute(
        "ALTER TABLE telemetry_readings_legacy_device "
        "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_device_telemetry "
        "ON telemetry_readings_legacy_device "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )
    op.execute(
        """
        CREATE VIEW device_telemetry AS
        SELECT
            id, tenant_id, device_id, timestamp,
            metric_name, metric_value, unit, metadata
        FROM telemetry_readings
        WHERE subject_kind = 'device'
        """
    )
