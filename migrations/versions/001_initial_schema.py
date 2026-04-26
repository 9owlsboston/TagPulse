"""Initial schema — devices table and tag_reads hypertable.

Revision ID: 001
Revises: None
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
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
    )

    # -- Convert tag_reads to TimescaleDB hypertable --
    op.execute(
        "SELECT create_hypertable('tag_reads', 'timestamp', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("tag_reads")
    op.drop_table("devices")
