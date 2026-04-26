"""Add users table and provisioning_key to tenants.

Revision ID: 014
Revises: 013
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Users table --
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="viewer"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("api_key_hash", sa.String(255), nullable=True),
        sa.Column("api_key_prefix", sa.String(10), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_unique_constraint("uq_users_tenant_email", "users", ["tenant_id", "email"])

    # Provisioning key on tenants
    op.add_column(
        "tenants",
        sa.Column("provisioning_key_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("provisioning_key_prefix", sa.String(10), nullable=True),
    )

    # RLS
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_users ON users "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    op.drop_column("tenants", "provisioning_key_prefix")
    op.drop_column("tenants", "provisioning_key_hash")
    op.drop_table("users")
