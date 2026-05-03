"""Sprint 15 Phase B — assets + asset_tag_bindings.

Adds:
- ``assets`` table (tenant-scoped, RLS) with ``parent_asset_id`` for carrier
  containment per docs/design/mobile-carriers-and-manifests.md §4.
- ``asset_tag_bindings`` table with ``binding_value`` + ``binding_kind`` from
  day one (per docs/design/assets-and-zones.md §3.2 naming note).
- Partial unique index enforcing one active binding per (tenant_id,
  binding_value).
- Non-unique global index on ``binding_value WHERE unbound_at IS NULL`` to
  power admin tag-collision tooling per assets-and-zones.md §11 Q3.

Revision ID: 018
Revises: 017
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Assets --
    op.create_table(
        "assets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "parent_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", JSONB, nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "external_ref", name="uq_assets_tenant_external_ref"
        ),
    )
    op.create_index(
        "ix_assets_tenant_type", "assets", ["tenant_id", "asset_type"]
    )
    op.create_index(
        "ix_assets_parent", "assets", ["parent_asset_id"]
    )
    op.create_check_constraint(
        "ck_assets_status",
        "assets",
        "status IN ('active','retired','lost')",
    )
    op.execute("ALTER TABLE assets ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_assets ON assets "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- Asset tag bindings --
    op.create_table(
        "asset_tag_bindings",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("binding_value", sa.String(256), nullable=False),
        sa.Column(
            "binding_kind",
            sa.String(20),
            nullable=False,
            server_default="epc",
        ),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("unbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
    )
    op.create_check_constraint(
        "ck_asset_tag_bindings_kind",
        "asset_tag_bindings",
        "binding_kind IN ('epc','tid','device')",
    )
    # Active-binding uniqueness per tenant (lookup at ingest time).
    op.execute(
        "CREATE UNIQUE INDEX ix_asset_tag_bindings_active "
        "ON asset_tag_bindings (tenant_id, binding_value) "
        "WHERE unbound_at IS NULL"
    )
    # Lookup by asset.
    op.create_index(
        "ix_asset_tag_bindings_by_asset",
        "asset_tag_bindings",
        ["asset_id", "bound_at"],
    )
    # Cross-tenant collision lookup (admin tooling). Non-unique by design.
    op.execute(
        "CREATE INDEX ix_asset_tag_bindings_global_value "
        "ON asset_tag_bindings (binding_value) "
        "WHERE unbound_at IS NULL"
    )
    op.execute("ALTER TABLE asset_tag_bindings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_asset_tag_bindings "
        "ON asset_tag_bindings "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_asset_tag_bindings "
        "ON asset_tag_bindings"
    )
    op.execute("ALTER TABLE asset_tag_bindings DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_asset_tag_bindings_global_value")
    op.drop_index(
        "ix_asset_tag_bindings_by_asset", table_name="asset_tag_bindings"
    )
    op.execute("DROP INDEX IF EXISTS ix_asset_tag_bindings_active")
    op.drop_constraint(
        "ck_asset_tag_bindings_kind", "asset_tag_bindings", type_="check"
    )
    op.drop_table("asset_tag_bindings")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_assets ON assets")
    op.execute("ALTER TABLE assets DISABLE ROW LEVEL SECURITY")
    op.drop_constraint("ck_assets_status", "assets", type_="check")
    op.drop_index("ix_assets_parent", table_name="assets")
    op.drop_index("ix_assets_tenant_type", table_name="assets")
    op.drop_table("assets")
