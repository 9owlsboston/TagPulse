"""Sprint 50 Phase A1: Tag registry — tags + tag_transfers tables.

Revision ID: 043
Revises: 042
Create Date: 2026-05-23

Implements the schema half of
[ADR-028 Tags as a first-class entity](../../docs/adr/028-tags-as-first-class-entity.md)
§"Decision" — the identity/ownership registry that answers
"what EPCs does this tenant own?". Closes the schema portion of
[reference-design-remediation plan](../../docs/design/reference-design-remediation.md)
row 2.14.

Adds:

- ``tags`` — tenant-scoped registry. One row per ``(tenant_id,
  epc_hex)``. ``epc_hex`` is the natural key (canonical: uppercase hex,
  no separators). ``gs1_uri`` is a *denormalized* parse of the GS1
  identifier and may be ``NULL`` for non-GS1 or unparseable EPCs; the
  partial index covers the populated subset for "give me every tag
  whose GS1 URI matches X" lookups. ``status`` and ``source`` are
  ``VARCHAR(16)`` (not native enums) per repo convention — see
  ``tenants.plan`` / ``devices.status`` / etc. Status transitions are
  validated in the service layer per ADR 028 §"Status enum" (matches
  ADR 019's ``category_type`` immutability pattern).
- ``tag_transfers`` — cross-tenant transfer audit log. ``request_id``
  groups all rows of one transfer request (one row per EPC). Indexed
  by request_id and by each tenant's outbound / inbound timeline.

Both tables get RLS — theatre-only per repo convention (the app
filters explicitly by ``tenant_id`` in every query; migrations run as
the table owner which bypasses RLS by default; see Sprint 7,
migration 007 style).

**Deliberately NOT added:**

- ``tag_batches`` table — batches are modelled as
  [ADR 020 labels](../../docs/adr/020-labels-first-class.md)
  with a reserved ``batch.*`` key namespace (ADR 028 OQ 5 resolution).
  The reserved-key registration + collision-detection migration lands
  in Phase A3.
- ``tag_reads.tag_known`` column — split into migration 044 (Phase A2)
  so the two concerns revert independently.
- Native PG enum types — ``VARCHAR(16)`` matches every other status /
  source column in the schema; CHECK constraints provide validation
  with much cheaper schema evolution.

Hot path: this migration is purely additive. The MQTT ingest path
does **not** read or write either of these tables (ADR 028
§"Hot-path interaction" — the critical constraint).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "043"
down_revision: str | None = "042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ADR 028 §"Status enum". `first_read` is intentionally NOT in the source
# enum (OQ 3 resolution — no auto-register on first read).
_TAG_STATUSES = ("registered", "active", "retired", "defective", "transferred_out")
_TAG_SOURCES = ("csv_import", "api", "backfill", "transfer_in")
_TRANSFER_STATUSES = ("requested", "completed", "failed")

# Canonical: uppercase hex, no separators. Min 16 hex chars (64-bit
# minimum tag — covers all real-world EPCs; the smallest standard EPC
# is GIAI-96 at 24 hex chars but we leave headroom for shorter test
# fixtures and proprietary tags). Max 128 matches the existing
# ``tag_reads.epc_hex VARCHAR(128)`` column added in migration 016.
_EPC_HEX_REGEX = r"^[0-9A-F]{16,128}$"


def upgrade() -> None:
    # -- 1. tags registry table --
    op.create_table(
        "tags",
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
        # Match the existing tag_reads.epc_hex VARCHAR(128) so joins
        # don't suffer an implicit cast. ADR 028 spec says TEXT; the
        # ceiling matters more than the type name.
        sa.Column("epc_hex", sa.String(128), nullable=False),
        sa.Column("gs1_uri", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "epc_hex", name="uq_tags_tenant_epc"),
    )
    op.create_check_constraint(
        "ck_tags_status",
        "tags",
        "status IN (" + ", ".join(f"'{value}'" for value in _TAG_STATUSES) + ")",
    )
    op.create_check_constraint(
        "ck_tags_source",
        "tags",
        "source IN (" + ", ".join(f"'{value}'" for value in _TAG_SOURCES) + ")",
    )
    op.create_check_constraint(
        "ck_tags_epc_hex_format",
        "tags",
        f"epc_hex ~ '{_EPC_HEX_REGEX}'",
    )
    # Partial index on the populated GS1 URI subset — drives
    # "every tag whose GS1 URI matches X" lookups without paying
    # index maintenance for the (expected majority) NULL rows.
    op.execute(
        "CREATE INDEX ix_tags_tenant_gs1_uri ON tags (tenant_id, gs1_uri) WHERE gs1_uri IS NOT NULL"
    )
    op.execute("ALTER TABLE tags ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tags ON tags "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- 2. tag_transfers audit log --
    # No tenant_id column — the row already names two tenants
    # (from_tenant_id, to_tenant_id) so an RLS predicate of the form
    # "current tenant participates" covers both visibility sides.
    op.create_table(
        "tag_transfers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "from_tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "to_tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("epc_hex", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tag_transfers_status",
        "tag_transfers",
        "status IN (" + ", ".join(f"'{value}'" for value in _TRANSFER_STATUSES) + ")",
    )
    op.create_check_constraint(
        "ck_tag_transfers_epc_hex_format",
        "tag_transfers",
        f"epc_hex ~ '{_EPC_HEX_REGEX}'",
    )
    op.create_check_constraint(
        "ck_tag_transfers_distinct_tenants",
        "tag_transfers",
        "from_tenant_id <> to_tenant_id",
    )
    op.create_check_constraint(
        "ck_tag_transfers_terminal_failure_reason",
        "tag_transfers",
        # failure_reason MUST be set when status='failed', and MUST NOT
        # be set when status='completed'. 'requested' is unconstrained
        # (could be filled later if the transfer fails).
        "(status = 'failed' AND failure_reason IS NOT NULL) "
        "OR (status = 'completed' AND failure_reason IS NULL) "
        "OR status = 'requested'",
    )
    op.create_check_constraint(
        "ck_tag_transfers_completed_at",
        "tag_transfers",
        # completed_at MUST be set iff the transfer is in a terminal
        # state.
        "(status IN ('completed', 'failed') AND completed_at IS NOT NULL) "
        "OR (status = 'requested' AND completed_at IS NULL)",
    )
    op.create_index("ix_tag_transfers_request_id", "tag_transfers", ["request_id"])
    op.create_index(
        "ix_tag_transfers_from_tenant",
        "tag_transfers",
        ["from_tenant_id", sa.text("requested_at DESC")],
    )
    op.create_index(
        "ix_tag_transfers_to_tenant",
        "tag_transfers",
        ["to_tenant_id", sa.text("requested_at DESC")],
    )
    op.execute("ALTER TABLE tag_transfers ENABLE ROW LEVEL SECURITY")
    # Either side of the transfer can see the row.
    op.execute(
        "CREATE POLICY tenant_isolation_tag_transfers ON tag_transfers "
        "USING ("
        "from_tenant_id = current_setting('app.current_tenant_id')::uuid "
        "OR to_tenant_id = current_setting('app.current_tenant_id')::uuid"
        ")"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_tag_transfers ON tag_transfers")
    op.execute("ALTER TABLE tag_transfers DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_tag_transfers_to_tenant", table_name="tag_transfers")
    op.drop_index("ix_tag_transfers_from_tenant", table_name="tag_transfers")
    op.drop_index("ix_tag_transfers_request_id", table_name="tag_transfers")
    op.drop_table("tag_transfers")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_tags ON tags")
    op.execute("ALTER TABLE tags DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_tags_tenant_gs1_uri")
    op.drop_table("tags")
