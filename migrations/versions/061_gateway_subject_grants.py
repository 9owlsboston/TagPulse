"""Sprint 81 — per-gateway approved-subject-set grants (C-6S9H).

A tenant admin authorizes a specific gateway device to relay telemetry for a
specific set of ``(subject_kind, subject_id)`` pairs. The telemetry-ingest guard
(I-75YC) then allows the gateway's own device subject **plus** its active grants.

Plain tenant-scoped association table (not a hypertable). Soft-revoke via
``revoked_at`` with a partial unique index over active rows.

See docs/design/gateway-subject-grants.md.

Revision ID: 061
Revises: 060
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gateway_subject_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "gateway_device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # One ACTIVE grant per (gateway, subject); revoked rows are kept for history.
    op.create_index(
        "uq_gateway_subject_grants_active",
        "gateway_subject_grants",
        ["tenant_id", "gateway_device_id", "subject_kind", "subject_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    # Hot path: fetch a gateway's active grant set.
    op.create_index(
        "ix_gateway_subject_grants_lookup",
        "gateway_subject_grants",
        ["tenant_id", "gateway_device_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute("ALTER TABLE gateway_subject_grants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_gateway_subject_grants "
        "ON gateway_subject_grants "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_gateway_subject_grants "
        "ON gateway_subject_grants"
    )
    op.execute("ALTER TABLE gateway_subject_grants DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_gateway_subject_grants_lookup", table_name="gateway_subject_grants"
    )
    op.drop_index(
        "uq_gateway_subject_grants_active", table_name="gateway_subject_grants"
    )
    op.drop_table("gateway_subject_grants")
