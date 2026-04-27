"""Add rules and alerts tables.

Revision ID: 006
Revises: 005
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Rules table --
    op.create_table(
        "rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("condition_type", sa.String(50), nullable=False),
        sa.Column("condition_config", JSONB, nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("action_config", JSONB, nullable=False),
        sa.Column("scope_device_id", UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
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
    op.create_index("ix_rules_tenant_id", "rules", ["tenant_id"])

    # -- Alerts table (hypertable) --
    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "rule_id", UUID(as_uuid=True), sa.ForeignKey("rules.id"), nullable=False
        ),
        sa.Column("device_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "severity", sa.String(20), nullable=False, server_default="warning"
        ),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("context", JSONB, nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="open"
        ),
        sa.Column(
            "triggered_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", "triggered_at"),
    )
    op.create_index("ix_alerts_tenant_id", "alerts", ["tenant_id"])
    op.create_index("ix_alerts_rule_id", "alerts", ["rule_id"])
    op.create_index("ix_alerts_triggered_at", "alerts", ["triggered_at"])

    # Convert alerts to hypertable
    op.execute(
        "SELECT create_hypertable('alerts', 'triggered_at', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("rules")
