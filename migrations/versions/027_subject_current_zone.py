"""Sprint 17a §5.2 — durable subject_current_zone state for the dwell worker.

Replaces the in-process ``DwellTracker._state`` map with a persisted table so
``zone.dwell_exceeded`` rules survive worker restarts and work in multi-worker
deployments. The previous in-process map remains as a hot path; this table is
its persistence backing — ``DwellTracker`` writes through on every event and
hydrates from the table on startup.

One row per ``(tenant_id, subject_kind, subject_id)`` — upserted on every
``subject.zone_changed`` event. RLS by ``tenant_id``.

Revision ID: 027
Revises: 026
Create Date: 2026-05-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subject_current_zone",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "subject_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "zone_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("zone_kind", sa.String(length=32), nullable=True),
        sa.Column(
            "entered_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "subject_kind", "subject_id"
        ),
    )
    op.create_index(
        "ix_subject_current_zone_zone",
        "subject_current_zone",
        ["tenant_id", "zone_id"],
        postgresql_where=sa.text("zone_id IS NOT NULL"),
    )
    # RLS — tenants can only see their own rows.
    op.execute("ALTER TABLE subject_current_zone ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY subject_current_zone_tenant_isolation "
        "ON subject_current_zone "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS subject_current_zone_tenant_isolation "
        "ON subject_current_zone"
    )
    op.drop_index("ix_subject_current_zone_zone", table_name="subject_current_zone")
    op.drop_table("subject_current_zone")
