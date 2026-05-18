"""Sprint 35 Phase A: Labels first-class entity + entity associations.

Revision ID: 039
Revises: 038
Create Date: 2026-05-17

Implements the schema half of [ADR-020 Labels first-class](../../docs/adr/020-labels-first-class.md).

Adds:

- ``labels`` table — tenant-scoped catalog of labels. Each label is
  scoped to one ``entity_type`` (``asset`` / ``site`` / ``zone`` /
  ``device`` / ``category``) so the same key can mean different things
  on different entity kinds. Uniqueness is ``(tenant_id, entity_type,
  lower(key))`` — case-insensitive per the ADR.
- ``entity_labels`` table — many-to-many association between a label
  and a polymorphic ``entity_id`` (no FK; cleanup happens at
  entity-delete time per ADR 020). FK to ``labels`` is ``ON DELETE
  RESTRICT`` to match the API contract (``DELETE /labels/{id}``
  returns 409 + ``association_count`` if the catalog row is still
  referenced; matches the Categories pattern from ADR 019).
- ``enforce_label_cap()`` trigger — BEFORE INSERT backstop that
  enforces the 30-labels-per-entity cap. The API layer also enforces
  this with an early reject so the trigger only fires on bypass
  paths (direct SQL, future bulk-associate jobs).
- RLS policies on both tables (theatre-only per repo convention; the
  app filters explicitly by ``tenant_id`` in every query, and
  migrations run as the table owner which bypasses RLS by default).

Note: ``created_by`` / ``updated_by`` are plain ``UUID NULL`` columns
without an FK. There is no ``users`` table in TagPulse (auth is
JWT-based and the user_id is an opaque claim); ``audit_logs.user_id``
follows the same pattern (see migration 015).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENTITY_TYPES = ("asset", "site", "zone", "device", "category")

# Key: 3-24 chars, alnum + _ . + $ (matches ADR 020 §"Validation rules").
_KEY_REGEX = r"^[A-Za-z0-9_.+$]{3,24}$"

# Value: 1-64 chars, alnum + _ . - (hyphen added vs the ADR draft so
# the ``warehouse-a`` style values in the §"Filter encoding" examples
# actually validate; matches k8s / AWS / GCP tag value conventions).
_VALUE_REGEX = r"^[A-Za-z0-9._-]{1,64}$"

# Color: optional ``#RRGGBB``.
_COLOR_REGEX = r"^#[0-9A-Fa-f]{6}$"


def upgrade() -> None:
    # -- 1. Create labels catalog table --
    op.create_table(
        "labels",
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
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("key", sa.String(24), nullable=False),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
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
    )
    op.create_index("ix_labels_tenant", "labels", ["tenant_id"])
    # Functional UNIQUE index — case-insensitive key uniqueness per
    # (tenant_id, entity_type). SQLAlchemy's UniqueConstraint cannot
    # express ``lower(key)``, so raw DDL.
    op.execute(
        "CREATE UNIQUE INDEX uq_labels_tenant_type_lower_key "
        "ON labels (tenant_id, entity_type, lower(key))"
    )
    op.create_check_constraint(
        "ck_labels_entity_type",
        "labels",
        "entity_type IN (" + ", ".join(f"'{value}'" for value in _ENTITY_TYPES) + ")",
    )
    op.create_check_constraint(
        "ck_labels_key_format",
        "labels",
        f"key ~ '{_KEY_REGEX}'",
    )
    op.create_check_constraint(
        "ck_labels_color_format",
        "labels",
        f"color IS NULL OR color ~ '{_COLOR_REGEX}'",
    )
    op.execute("ALTER TABLE labels ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_labels ON labels "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # -- 2. Create entity_labels association table --
    # entity_id is polymorphic (no FK) — matches assets / sites /
    # zones / devices / categories depending on the parent label's
    # entity_type. Orphan cleanup happens in the entity-delete
    # handlers (Phase B). The trigger below enforces the 30-per-entity
    # cap regardless of entity_type.
    op.create_table(
        "entity_labels",
        sa.Column(
            "label_id",
            UUID(as_uuid=True),
            sa.ForeignKey("labels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("label_id", "entity_id", name="pk_entity_labels"),
    )
    op.create_index("ix_entity_labels_entity", "entity_labels", ["entity_id"])
    op.create_check_constraint(
        "ck_entity_labels_value_format",
        "entity_labels",
        f"value ~ '{_VALUE_REGEX}'",
    )
    op.execute("ALTER TABLE entity_labels ENABLE ROW LEVEL SECURITY")
    # RLS on the association table goes via the parent label's tenant.
    op.execute(
        "CREATE POLICY tenant_isolation_entity_labels ON entity_labels "
        "USING (EXISTS (SELECT 1 FROM labels l "
        "WHERE l.id = entity_labels.label_id "
        "AND l.tenant_id = current_setting('app.current_tenant_id')::uuid))"
    )

    # -- 3. 30-per-entity cap trigger --
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_label_cap() RETURNS TRIGGER AS $$
        BEGIN
          IF (SELECT count(*) FROM entity_labels WHERE entity_id = NEW.entity_id) >= 30 THEN
            RAISE EXCEPTION 'label cap exceeded (max 30 labels per entity)'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_enforce_label_cap "
        "BEFORE INSERT ON entity_labels "
        "FOR EACH ROW EXECUTE FUNCTION enforce_label_cap()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_enforce_label_cap ON entity_labels")
    op.execute("DROP FUNCTION IF EXISTS enforce_label_cap()")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_entity_labels ON entity_labels")
    op.execute("ALTER TABLE entity_labels DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_entity_labels_entity", table_name="entity_labels")
    op.drop_table("entity_labels")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_labels ON labels")
    op.execute("ALTER TABLE labels DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS uq_labels_tenant_type_lower_key")
    op.drop_index("ix_labels_tenant", table_name="labels")
    op.drop_table("labels")
