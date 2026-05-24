"""Sprint 50 Phase C3: two-person rule above tenant-configurable threshold.

Revision ID: 047
Revises: 046
Create Date: 2026-05-23

Implements [ADR-028 §"Governance" rule 4](../../docs/adr/028-tags-as-first-class-entity.md):
bulk ops over ``tenants.tag_bulk_two_person_threshold`` (default
10 000) create a ``pending`` request that a second admin must
approve before it executes. Generalises the
``tag_transfers.status`` pattern from Phase B into a single table
keyed by ``operation`` so future bulk endpoints (C4 bulk PATCH /
retire) plug in without another migration.

Two artefacts:

1. ``tenants.tag_bulk_two_person_threshold`` — INT NOT NULL
   DEFAULT 10 000. Match the ADR. Existing tenants backfill on
   upgrade; operators with a documented onboarding flow can lower
   it via ``PATCH /tenant-config`` (the column joins the rest of
   the per-tenant knobs from Sprint 22 A4 + Sprint 50 C1).

2. ``pending_bulk_operations`` — the generic pending-request
   table. Carries the raw CSV bytes (``payload``) so the second
   admin doesn't need to re-upload — they hit
   ``POST /bulk-operations/{id}/approve`` and the server re-parses
   + re-hashes the stored bytes (with the original content_hash
   as a tamper guard) and dispatches by ``operation`` string.
   ``status`` ∈ ``{pending, approved, rejected, executed, expired}``
   with the obvious state machine (pending → approved → executed
   is the happy path; pending → rejected is the deny path;
   pending → expired is the timeout path swept lazily on
   ``approve``). ``expires_at`` is 24 h from creation — two-person
   review needs more wall-clock than the 15-min C2 token TTL.

RLS theatre per the repo convention (the app runs as table owner
which bypasses RLS by default; see Sprint 7). Index on
``(tenant_id, status, expires_at)`` drives the future
``GET /bulk-operations?status=pending`` admin UI list.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PENDING_STATUSES = ("pending", "approved", "rejected", "executed", "expired")


def upgrade() -> None:
    # 1. Tenant-scoped threshold knob.
    op.add_column(
        "tenants",
        sa.Column(
            "tag_bulk_two_person_threshold",
            sa.Integer(),
            nullable=False,
            server_default="10000",
        ),
    )

    # 2. Generic pending-bulk-op table.
    op.create_table(
        "pending_bulk_operations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Operation discriminator. Mirrors the C2 token store's
        # ``operation`` axis so the same string namespace governs
        # both the in-memory token and the persisted pending row.
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        # Requester / approver. NULL on requester side covers
        # tenant-API-key actors (no Entra user); approver is always
        # a real user (admin role) and is therefore NOT NULL once
        # set — but NULL while the row is still pending.
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Content fingerprint from C2 (same hash algorithm).
        # Stored so ``approve`` can verify the re-parsed payload
        # still matches what the first admin previewed (tamper guard
        # — a malicious in-flight DB edit changing ``payload`` would
        # flip the hash and the approve path raises 409).
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("sample", JSONB(), nullable=False),
        sa.Column("payload", BYTEA(), nullable=False),
        # ``request_id`` is set at approval time so audit log
        # entries from approve + the executed bulk op cross-link.
        # NULL while pending.
        sa.Column("request_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_pending_bulk_operations_status",
        "pending_bulk_operations",
        "status IN (" + ", ".join(f"'{value}'" for value in _PENDING_STATUSES) + ")",
    )
    op.create_check_constraint(
        "ck_pending_bulk_operations_row_count_positive",
        "pending_bulk_operations",
        "row_count > 0",
    )
    # Approved/rejected/executed rows MUST carry decided_by + decided_at;
    # pending rows MUST NOT. Mirrors the
    # ``ck_tag_transfers_completed_at`` invariant style from migration 043.
    op.create_check_constraint(
        "ck_pending_bulk_operations_terminal_decided",
        "pending_bulk_operations",
        "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL) "
        "OR (status IN ('approved', 'rejected', 'executed') "
        "    AND decided_by IS NOT NULL AND decided_at IS NOT NULL) "
        "OR (status = 'expired')",
    )
    op.execute(
        "CREATE INDEX ix_pending_bulk_operations_tenant_status_expires "
        "ON pending_bulk_operations (tenant_id, status, expires_at)"
    )
    op.execute("ALTER TABLE pending_bulk_operations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_pending_bulk_operations ON pending_bulk_operations "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_pending_bulk_operations ON pending_bulk_operations"
    )
    op.drop_table("pending_bulk_operations")
    op.drop_column("tenants", "tag_bulk_two_person_threshold")
