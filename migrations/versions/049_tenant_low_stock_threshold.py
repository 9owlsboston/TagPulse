"""Sprint 54 Phase 54.3: per-tenant low-stock threshold.

Revision ID: 049
Revises: 048
Create Date: 2026-05-24

Adds ``tenants.low_stock_threshold`` (INT NOT NULL DEFAULT 3) so the
new ``GET /dashboard/summary`` endpoint's ``low_stock_count`` field
can be tuned per tenant without code changes. A product counts as
"low stock" when the number of active (``state='in_stock' AND
consumed_at IS NULL``) ``stock_items`` for that product is strictly
less than the tenant's threshold. The default (3) matches the
operator-side guess captured during Sprint 54 planning; tenants
override it via ``PATCH /tenant/config``.

Joins the existing per-tenant integer knobs from Sprint 22 A4
(``rate_limit_overrides``), Sprint 50 C1 (``tag_bulk_import_rate_limit``),
and Sprint 50 C3 (``tag_bulk_two_person_threshold``) — same pattern,
same upgrade path. Existing tenants backfill via the server_default.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "049"
down_revision: str | None = "048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "low_stock_threshold",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "low_stock_threshold")
