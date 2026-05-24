"""Sprint 50 Phase C5: unified audit-log shape for bulk ops.

Revision ID: 048
Revises: 047
Create Date: 2026-05-23

Implements [ADR-028 §"Governance" rule 7](../../docs/adr/028-tags-as-first-class-entity.md):
"Single audit log keyed on ``(actor, action, batch, count, request_id)``
for every bulk op." Phases C1–C4 each shoved these keys into the
``audit_logs.changes`` JSONB blob with the explicit promise that C5
would hoist them into top-level columns. This migration is that hoist
— pure additive, no data rewrite (the JSONB payload stays populated
in parallel so historical greps don't break).

Five new nullable columns on ``audit_logs``:

- ``request_id UUID NULL`` — already encoded in ``resource_id`` for the
  C1/C4 "executed" rows (``resource_type='tag'``, ``resource_id=<rid>``),
  but unindexed there because the column has cross-resource cardinality.
  As a dedicated column with its own partial index the operator query
  "show me every audit entry for request <X>" (correlating the
  ``tag.bulk_*_requested`` 202 row with its ``tag.bulk_*ed`` execute
  row + the ``tags.import.approved`` decide row) becomes one index
  scan instead of three full table scans.

- ``batch TEXT NULL`` — the resolved ``labels[batch]`` value when the
  bulk op was scope-by-batch. NULL for ``epc_list`` scope (no shared
  batch label) and for ``tags.import`` (the C1 importer writes a fresh
  tag set without batch attribution; batch labels are attached
  separately via ``POST /labels`` after the import lands). Operators
  asking "who touched batch B-001 last month" was a §7 motivating
  use case.

- ``count INTEGER NULL CHECK (count >= 0)`` — affected-row count: the
  ``rows_created`` for tag.bulk_imported, the ``updated`` for
  tag.bulk_patched / tag.bulk_retired, the ``epc_count`` for
  tag_transfer.requested, the ``row_count`` for the pending-op decide
  rows. Hoisted because the ADR's "blast radius" reviews are count-
  based and a JSONB->>'count'::int filter doesn't use any index.

- ``pending_id UUID NULL`` — FK ``pending_bulk_operations.id`` ON
  DELETE SET NULL. Populated on both the C3-era ``tag.bulk_import_requested``
  audit row (pending row just minted) and the corresponding
  ``tag.bulk_imported`` audit row that executes via approve (so the
  cross-link is symmetric). FK so a cascade-deleted pending op
  doesn't leave a dangling reference, but ON DELETE SET NULL because
  losing the link is preferable to losing the audit entry itself.

- ``approved_by UUID NULL`` — FK ``users.id`` ON DELETE SET NULL.
  The second-admin's user id for two-person-rule executions; NULL on
  every direct (sub-threshold) bulk op. Lets reviewers ask "who
  approved batch X" without parsing JSONB.

Two partial indexes covering the ADR §7 query patterns:

- ``(tenant_id, request_id) WHERE request_id IS NOT NULL`` — the
  request-correlation index above.
- ``(tenant_id, batch) WHERE batch IS NOT NULL`` — the batch-history
  query above.

Both are partial so they don't bloat for the 99 % of audit rows that
have nothing to do with bulk ops (device tokens, label changes, etc).

Also widens ``audit_logs.action`` from ``VARCHAR(20)`` → ``VARCHAR(40)``
as a hygiene fix bundled with this column-shape work: the C1/C3/C4
action strings introduced earlier in Sprint 50
(``tag.bulk_import_requested`` = 24c,
``tag.bulk_retire_requested`` = 25c) already exceed the original
20-char cap that migration 012 set in Sprint 12. Test suites use
in-memory fakes so the discrepancy never surfaced, but a live PG
instance would reject the longer values with ``value too long``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "048"
down_revision: str | None = "047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "action",
        existing_type=sa.String(20),
        type_=sa.String(40),
        existing_nullable=False,
    )
    op.add_column(
        "audit_logs",
        sa.Column("request_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("batch", sa.Text(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "pending_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pending_bulk_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_audit_logs_count_non_negative",
        "audit_logs",
        "count IS NULL OR count >= 0",
    )
    op.execute(
        "CREATE INDEX ix_audit_logs_tenant_request_id "
        "ON audit_logs (tenant_id, request_id) WHERE request_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_audit_logs_tenant_batch "
        "ON audit_logs (tenant_id, batch) WHERE batch IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_tenant_batch")
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_tenant_request_id")
    op.drop_constraint("ck_audit_logs_count_non_negative", "audit_logs", type_="check")
    op.drop_column("audit_logs", "approved_by")
    op.drop_column("audit_logs", "pending_id")
    op.drop_column("audit_logs", "count")
    op.drop_column("audit_logs", "batch")
    op.drop_column("audit_logs", "request_id")
    op.alter_column(
        "audit_logs",
        "action",
        existing_type=sa.String(40),
        type_=sa.String(20),
        existing_nullable=False,
    )
