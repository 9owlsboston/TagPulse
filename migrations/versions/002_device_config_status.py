"""Add device configuration, firmware, connection state, and last_seen columns.

Revision ID: 002
Revises: 001
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("configuration", JSONB, nullable=True))
    op.add_column("devices", sa.Column("firmware_version", sa.String(50), nullable=True))
    op.add_column(
        "devices",
        sa.Column(
            "connection_state",
            sa.String(50),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "devices",
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("devices", "last_seen")
    op.drop_column("devices", "connection_state")
    op.drop_column("devices", "firmware_version")
    op.drop_column("devices", "configuration")
