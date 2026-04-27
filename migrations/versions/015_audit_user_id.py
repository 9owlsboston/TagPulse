"""Add user_id column to audit_logs table.

Revision ID: 015
Revises: 014
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "user_id")
