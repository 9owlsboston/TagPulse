"""Add multi-tenancy: tenants table, tenant_id FKs, usage and quota tables.

Revision ID: 005
Revises: 004
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Tenants table --
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("plan", sa.String(50), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # -- Add tenant_id to devices --
    op.add_column(
        "devices",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])
    op.create_foreign_key(
        "fk_devices_tenant_id", "devices", "tenants", ["tenant_id"], ["id"]
    )

    # -- Add tenant_id to tag_reads --
    op.add_column(
        "tag_reads",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_tag_reads_tenant_id", "tag_reads", ["tenant_id"])
    op.create_foreign_key(
        "fk_tag_reads_tenant_id", "tag_reads", "tenants", ["tenant_id"], ["id"]
    )

    # -- Tenant usage detail --
    op.create_table(
        "tenant_usage_detail",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("usage_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dimension", sa.String(50), nullable=False),
        sa.Column("quantity", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "usage_date", "dimension"),
    )

    # -- Tenant quotas --
    op.create_table(
        "tenant_quotas",
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("dimension", sa.String(50), nullable=False),
        sa.Column("max_quantity", sa.BigInteger, nullable=False),
        sa.Column("period", sa.String(20), nullable=False, server_default="daily"),
        sa.Column(
            "action_on_exceed", sa.String(20), nullable=False, server_default="throttle"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "dimension"),
    )


def downgrade() -> None:
    op.drop_table("tenant_quotas")
    op.drop_table("tenant_usage_detail")
    op.drop_constraint("fk_tag_reads_tenant_id", "tag_reads", type_="foreignkey")
    op.drop_index("ix_tag_reads_tenant_id", table_name="tag_reads")
    op.drop_column("tag_reads", "tenant_id")
    op.drop_constraint("fk_devices_tenant_id", "devices", type_="foreignkey")
    op.drop_index("ix_devices_tenant_id", table_name="devices")
    op.drop_column("devices", "tenant_id")
    op.drop_table("tenants")
