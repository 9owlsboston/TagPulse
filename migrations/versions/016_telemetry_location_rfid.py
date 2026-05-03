"""Telemetry, location, and RFID identity foundations (Sprint 14).

Revision ID: 016
Revises: 015
Create Date: 2026-05-02

Adds:
- Location + RFID identity columns on `tag_reads`
- `device_telemetry` hypertable with RLS
- `telemetry_quarantine` table for unknown / out-of-range readings

See docs/design/telemetry-and-location.md and docs/design/rfid-tag-data-model.md.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Extend tag_reads with location + RFID identity columns --
    op.add_column(
        "tag_reads",
        sa.Column("latitude", sa.Float, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("longitude", sa.Float, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("location_accuracy_m", sa.Float, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("location_source", sa.String(20), nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("epc", sa.String(256), nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("epc_hex", sa.String(128), nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("epc_scheme", sa.String(32), nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("epc_decoded", JSONB, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("tid", sa.String(64), nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("user_memory_hex", sa.Text, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("tag_data", JSONB, nullable=True),
    )
    op.add_column(
        "tag_reads",
        sa.Column("reader_antenna", sa.SmallInteger, nullable=True),
    )

    # Partial index — supports "reads with location" without bloating writes.
    op.execute(
        "CREATE INDEX ix_tag_reads_location "
        "ON tag_reads (tenant_id, timestamp DESC) "
        "WHERE latitude IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_tag_reads_epc "
        "ON tag_reads (tenant_id, epc, timestamp DESC) "
        "WHERE epc IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_tag_reads_tid "
        "ON tag_reads (tenant_id, tid, timestamp DESC) "
        "WHERE tid IS NOT NULL"
    )

    # -- device_telemetry hypertable --
    op.create_table(
        "device_telemetry",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    op.execute(
        "SELECT create_hypertable('device_telemetry', 'timestamp', if_not_exists => TRUE)"
    )
    op.create_index(
        "ix_device_telemetry_lookup",
        "device_telemetry",
        ["tenant_id", "device_id", "metric_name", sa.text("timestamp DESC")],
    )
    op.execute("ALTER TABLE device_telemetry ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_device_telemetry ON device_telemetry "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- telemetry_quarantine (lightweight, capped retention via Timescale policy) --
    op.create_table(
        "telemetry_quarantine",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_telemetry_quarantine_tenant",
        "telemetry_quarantine",
        ["tenant_id", sa.text("received_at DESC")],
    )
    op.execute("ALTER TABLE telemetry_quarantine ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_telemetry_quarantine ON telemetry_quarantine "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_telemetry_quarantine "
        "ON telemetry_quarantine"
    )
    op.execute("ALTER TABLE telemetry_quarantine DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_telemetry_quarantine_tenant", table_name="telemetry_quarantine")
    op.drop_table("telemetry_quarantine")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_device_telemetry ON device_telemetry"
    )
    op.execute("ALTER TABLE device_telemetry DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_device_telemetry_lookup", table_name="device_telemetry")
    op.drop_table("device_telemetry")

    op.execute("DROP INDEX IF EXISTS ix_tag_reads_tid")
    op.execute("DROP INDEX IF EXISTS ix_tag_reads_epc")
    op.execute("DROP INDEX IF EXISTS ix_tag_reads_location")
    for col in (
        "reader_antenna",
        "tag_data",
        "user_memory_hex",
        "tid",
        "epc_decoded",
        "epc_scheme",
        "epc_hex",
        "epc",
        "location_source",
        "location_accuracy_m",
        "longitude",
        "latitude",
    ):
        op.drop_column("tag_reads", col)
