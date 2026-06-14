"""Sprint 60 increment 2 — ``user_ui_prefs`` (ADR-032 §3 user-override layer).

Lands the *user* layer of the Configurable UI four-layer resolve
(System → Tenant → Role → **User**). Increment 1 served the system default
only; this table is the persistence for ``PUT /ui-config/me`` and the override
``GET /ui-config`` folds in for the caller.

One additive table, no behavioural change to anything that exists:

- ``user_ui_prefs`` — ``user_id`` PK (one row per user, low-cardinality, no
  history), ``tenant_id`` FK for scoping/audit, ``prefs`` JSONB holding the
  **sparse** per-leaf override (subset of the ADR-032 §4 document — missing
  keys fall through to the layer below), ``updated_at`` touch column.

**No RLS.** This table is the direct sibling of ``users`` (same ``user_id`` PK
grain), and ``users`` carries no row-level security — the request path sets no
``app.current_tenant_id`` GUC (it filters tenant explicitly), so an RLS policy
keyed on that GUC would error on every request-path read. The repository
filters by the globally-unique ``user_id`` PK, which already pins the row to
one user (hence one tenant); ``tenant_id`` is stored for audit + the future
"reset to team default" fall-through, not for isolation.

"Reset to team default" (ADR-032 §2) = delete the row → the user falls back to
role/tenant/system automatically; modelled as ``ON DELETE CASCADE`` from
``users`` so a deleted user can never orphan a prefs row.

Revision ID: 052
Revises: 051
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_ui_prefs",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prefs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_ui_prefs")
