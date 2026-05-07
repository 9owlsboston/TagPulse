"""Sprint 13b — multi-tier foundations.

Adds ``tenants.db_pool_key`` so the per-request middleware can route a tenant
to a dedicated pool when one becomes available. v1 leaves every tenant on the
default ``shared_default`` pool; promoting a tenant to a sovereign cluster is
later a single ``UPDATE`` rather than a code change.

Per docs/design/storage-strategy.md §6 Q2 and docs/roadmap.md Sprint 13b.

Revision ID: 023
Revises: 022
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "db_pool_key",
            sa.String(length=64),
            nullable=False,
            server_default="shared_default",
        ),
    )
    # Index keeps the per-pool tenant-list query (used by ops dashboards and
    # the future shared→sovereign promotion runbook) cheap.
    op.create_index(
        "ix_tenants_db_pool_key", "tenants", ["db_pool_key"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_db_pool_key", table_name="tenants")
    op.drop_column("tenants", "db_pool_key")
