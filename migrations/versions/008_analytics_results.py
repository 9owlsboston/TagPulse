"""Add analytics_results table.

Revision ID: 008
Revises: 007
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analytics_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("module_name", sa.String(100), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analytics_results_tenant_id", "analytics_results", ["tenant_id"])
    op.create_index("ix_analytics_results_module", "analytics_results", ["module_name"])
    op.create_index("ix_analytics_results_device", "analytics_results", ["device_id"])
    op.create_index("ix_analytics_results_computed", "analytics_results", ["computed_at"])


def downgrade() -> None:
    op.drop_table("analytics_results")
