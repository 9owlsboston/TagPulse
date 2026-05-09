"""Sprint 27 C3: add api_key_created_at to users table.

Revision ID: 034
Revises: 033
Create Date: 2026-05-08

Tracks when the current API key was generated so operators can tell
how old a key is from the user detail page.
"""

from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "api_key_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "api_key_created_at")
