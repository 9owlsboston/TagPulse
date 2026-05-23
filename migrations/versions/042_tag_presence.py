"""Sprint 46: tag_presence current-state table for the v2 edge wire format.

Revision ID: 042
Revises: 041
Create Date: 2026-05-23

Implements [ADR-026 Server-side presence model](../../docs/adr/026-presence-model.md)
§3.1 storage schema, ratifying [docs/design/edge-wire-format-v2.md §4.1].

Adds a NEW table ``tag_presence`` that stores the *current* per-reader
presence of each EPC. Distinct from ``tag_reads`` (the existing
hypertable) which keeps the time-series audit trail of every observation;
``tag_presence`` is the live edge — one row per
``(tenant_id, device_id, epc)`` with ``status`` flipping between
``'present'`` and ``'gone'`` as snaps and deltas arrive.

Deliberately NOT a hypertable per ADR-026 §3.1 — row count is bounded
by EPC fleet size, not by time, and every update is on the current row.

No ``last_seq`` / ``suspect`` columns: there is no per-cycle counter on
the v2 wire (§3.1) and no buffered-snap state to be suspicious about —
reconciliation is synchronous on snap receipt (§4.2). See ADR-026 §3.2.

Indexes:

- ``idx_tag_presence_active`` partial on ``(tenant_id, device_id)``
  WHERE ``status='present'`` — drives "what's at this reader right now."
- ``idx_tag_presence_tenant_epc`` partial on ``(tenant_id, epc)``
  WHERE ``status='present'`` — drives "where is this EPC now."

RLS by ``tenant_id`` per repo convention (Sprint 7, migration 007 style):
session GUC ``app.current_tenant_id`` gates row visibility. Subscriber
sets the GUC inside the ``tenant_context()`` per-message scope before
running the reconciler.

The downgrade is destructive (drops the table and all presence state).
``tag_reads`` is unaffected by either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PRESENCE_STATUSES = ("present", "gone")


def upgrade() -> None:
    op.create_table(
        "tag_presence",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("epc", sa.String(length=124), nullable=False),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_rssi", sa.SmallInteger(), nullable=True),
        sa.Column("last_antenna", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "device_id", "epc", name="pk_tag_presence"),
    )

    op.create_check_constraint(
        "ck_tag_presence_status",
        "tag_presence",
        "status IN (" + ", ".join(f"'{value}'" for value in _PRESENCE_STATUSES) + ")",
    )

    op.create_index(
        "idx_tag_presence_active",
        "tag_presence",
        ["tenant_id", "device_id"],
        postgresql_where=sa.text("status = 'present'"),
    )
    op.create_index(
        "idx_tag_presence_tenant_epc",
        "tag_presence",
        ["tenant_id", "epc"],
        postgresql_where=sa.text("status = 'present'"),
    )

    # RLS — tenants can only see their own rows. Subscriber sets the
    # session GUC inside tenant_context() before reading or writing.
    op.execute("ALTER TABLE tag_presence ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tag_presence ON tag_presence "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_tag_presence ON tag_presence")
    op.drop_index("idx_tag_presence_tenant_epc", table_name="tag_presence")
    op.drop_index("idx_tag_presence_active", table_name="tag_presence")
    op.drop_table("tag_presence")
