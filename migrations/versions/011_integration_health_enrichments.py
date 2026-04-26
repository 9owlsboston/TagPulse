"""Add health_status, filters, enrichments, consecutive_failures to integrations.

Revision ID: 011
Revises: 010
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column(
            "health_status",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "integrations",
        sa.Column("filters", JSONB, nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("enrichments", JSONB, nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "consecutive_failures",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("integrations", "consecutive_failures")
    op.drop_column("integrations", "enrichments")
    op.drop_column("integrations", "filters")
    op.drop_column("integrations", "health_status")
