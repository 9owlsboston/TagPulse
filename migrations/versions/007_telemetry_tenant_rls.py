"""Add tenant_id to telemetry_models and RLS policies on tenant-scoped tables.

Revision ID: 007
Revises: 006
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_SCOPED_TABLES = ["devices", "tag_reads", "rules", "alerts", "telemetry_models"]


def upgrade() -> None:
    # -- Add tenant_id to telemetry_models --
    op.add_column(
        "telemetry_models",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_telemetry_models_tenant_id", "telemetry_models", ["tenant_id"]
    )
    op.create_foreign_key(
        "fk_telemetry_models_tenant_id",
        "telemetry_models",
        "tenants",
        ["tenant_id"],
        ["id"],
    )

    # Drop the old unique constraint on device_type (now unique per tenant)
    op.drop_constraint(
        "telemetry_models_device_type_key", "telemetry_models", type_="unique"
    )
    op.create_unique_constraint(
        "uq_telemetry_models_tenant_device_type",
        "telemetry_models",
        ["tenant_id", "device_type"],
    )

    # -- RLS policies on all tenant-scoped tables --
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
        )


def downgrade() -> None:
    # Drop RLS policies
    for table in reversed(TENANT_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Revert telemetry_models changes
    op.drop_constraint(
        "uq_telemetry_models_tenant_device_type",
        "telemetry_models",
        type_="unique",
    )
    op.create_unique_constraint(
        "telemetry_models_device_type_key", "telemetry_models", ["device_type"]
    )
    op.drop_constraint(
        "fk_telemetry_models_tenant_id", "telemetry_models", type_="foreignkey"
    )
    op.drop_index("ix_telemetry_models_tenant_id", table_name="telemetry_models")
    op.drop_column("telemetry_models", "tenant_id")
