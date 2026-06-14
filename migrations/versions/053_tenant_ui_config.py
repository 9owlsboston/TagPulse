"""Sprint 60 increment 3 — ``tenants.ui_config`` (ADR-032 §3 tenant+role layers).

Lands the *tenant* and *role* default layers of the Configurable UI four-layer
resolve (System → **Tenant** → **Role** → User). Increment 1 served the system
default, increment 2 added the user override; this column is the persistence
for ``PUT /ui-config/tenant`` and ``PUT /ui-config/role/{role}``, and the two
layers ``GET /ui-config`` now folds in beneath the user override.

One additive nullable column, no behavioural change to anything that exists:

- ``tenants.ui_config`` JSONB, ``NULL`` = pure system default. Reuses the
  established tenant-JSONB precedent (``tile_provider`` / ``rate_limit_overrides``
  / ``position_strategy``) — this **resolves the ADR-032 D8 storage question by
  precedent: JSONB, not a relational schema**. Shape (ADR-032 §3): the
  tenant-default leaves live at the top level and the role layer is keyed
  inside a reserved ``roles`` sub-object::

      {"theme": {...}, "columns": {...},
       "roles": {"viewer": {"columns": {...}}, "editor": {...}}}

No RLS is added — the ``tenants`` table carries none (the request path filters
tenant explicitly by id, it sets no ``app.current_tenant_id`` GUC), exactly as
the sibling ``tile_provider`` / ``rate_limit_overrides`` columns already rely
on. The ``locked`` leaf-pin (ADR-032 §2) is deliberately **not** modelled yet —
it lands with a later increment once the floor/persona layers are proven.

Revision ID: 053
Revises: 052
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("ui_config", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "ui_config")
