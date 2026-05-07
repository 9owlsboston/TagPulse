"""Sprint 15b — case/pallet containment for stock items.

Adds ``stock_items.parent_stock_item_id`` (self-FK) per
[mobile-carriers-and-manifests.md §4.3](../../docs/design/mobile-carriers-and-manifests.md)
so an SSCC pallet can contain SGTIN cases without coupling to the asset
hierarchy. Nullable: existing rows are top-level by definition. Indexed for
recursive manifest CTE (mirrors ``assets.parent_asset_id``).

Revision ID: 028
Revises: 027
Create Date: 2026-05-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_items",
        sa.Column(
            "parent_stock_item_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stock_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_stock_items_parent",
        "stock_items",
        ["parent_stock_item_id"],
        postgresql_where=sa.text("parent_stock_item_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_stock_items_parent", table_name="stock_items")
    op.drop_column("stock_items", "parent_stock_item_id")
