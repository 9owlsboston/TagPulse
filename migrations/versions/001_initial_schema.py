"""Initial schema — devices table and tag_reads hypertable.

Revision ID: 001
Revises: None
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- TimescaleDB extension --
    # Azure Postgres Flexible Server requires TIMESCALEDB to be on the
    # `azure.extensions` allow-list (handled in deploy/azure/bicep/modules/postgres.bicep)
    # AND for the database itself to run `CREATE EXTENSION` before any
    # `create_hypertable()` call. Local docker-compose ships the extension
    # pre-created in its init script, but the cloud DB starts empty.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # -- Devices table (relational) --
    op.create_table(
        "devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("device_type", sa.String(50), nullable=False, server_default="rfid_reader"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # -- Tag reads table --
    op.create_table(
        "tag_reads",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("tag_id", sa.Text, nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("signal_strength", sa.Float, nullable=True),
        sa.Column("sensor_data", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )

    # -- Convert tag_reads to TimescaleDB hypertable --
    op.execute("SELECT create_hypertable('tag_reads', 'timestamp', if_not_exists => TRUE)")


def downgrade() -> None:
    op.drop_table("tag_reads")
    op.drop_table("devices")
