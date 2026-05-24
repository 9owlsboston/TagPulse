"""Sprint 50 Phase A3: reserve batch.* label namespace for tag entities.

Revision ID: 045
Revises: 044
Create Date: 2026-05-23

Implements [ADR-028 OQ 5 resolution](../../docs/adr/028-tags-as-first-class-entity.md)
+ Sprint 50 locked risk #2 (reserved-label-key collision policy =
refuse + manual intervention). Companion to migration 043 (tags
registry) and 044 (tag_known).

What this migration does:

1. **Extends ``labels.entity_type`` CHECK constraint** to include
   ``'tag'`` (was: ``asset|site|zone|device|category``). Tags are
   now a first-class labelable entity per ADR 020 + ADR 028.
2. **Refuses to run on any tenant that has labels colliding with
   the reserved ``batch.*`` namespace** (key = ``batch`` OR key
   starting with ``batch.``), across **all** entity_types. Per the
   Sprint 50 locked policy, there is no auto-rename and no silent
   coexistence — operators must consciously reconcile via the rename
   runbook at
   [docs/runbooks/reserved-label-key-collision.md](../../docs/runbooks/reserved-label-key-collision.md)
   before redeploying.
3. **Backfills the 4 reserved label rows for every existing tenant**:
   ``batch`` / ``batch.received_at`` / ``batch.description`` /
   ``batch.supplier``, all scoped to ``entity_type='tag'``. These
   rows are inserted by the migration as the system actor (created_by
   = NULL) and are protected from delete by API-layer policy (Phase B).

What this migration does NOT do:

- **Does NOT add per-tenant onboarding hooks** — that's Python code in
  the tenant-provisioning service (Phase A3 follow-up). New tenants
  created after this migration runs MUST seed these 4 rows via the
  same mechanism, or their ``tags`` labels API will reject ``batch.*``
  bindings.
- **Does NOT enforce the reservation at the DB level beyond the
  partial unique-key constraint that already exists on ``labels``**.
  The reservation is enforced in the service layer (Phase B): the
  labels API rejects user-initiated CREATE / UPDATE / DELETE for any
  key matching the reserved namespace regardless of entity_type.

Failure mode (collision detected):
    psycopg2.errors.RaiseException: reserved label-key collision
    detected. Tenants <slug,...> hold labels under the batch.*
    namespace reserved by ADR 028 for Sprint 50 tag-batch grouping.
    See docs/runbooks/reserved-label-key-collision.md for the rename
    procedure. Migration refused — re-run after reconciliation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "045"
down_revision: str | None = "044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Must include all values from migration 039 plus 'tag'.
_ENTITY_TYPES_V2 = ("asset", "site", "zone", "device", "category", "tag")

# Reserved label keys for tag-batch grouping (ADR 028 §"Batches:
# labels, not a table"). All ≤ 24 chars to satisfy migration 039's
# ck_labels_key_format regex.
_RESERVED_BATCH_KEYS = (
    "batch",
    "batch.received_at",
    "batch.description",
    "batch.supplier",
)


def upgrade() -> None:
    bind = op.get_bind()

    # -- 1. Collision detection (refuse policy per Sprint 50 risk lock) --
    # Any tenant with a label keyed 'batch' or starting 'batch.' under
    # ANY entity_type is a collision. We surface the colliding tenant
    # slugs to make the operator's reconciliation work obvious.
    colliding = bind.execute(
        sa.text(
            """
            SELECT DISTINCT t.slug
              FROM labels l
              JOIN tenants t ON t.id = l.tenant_id
             WHERE lower(l.key) = 'batch'
                OR lower(l.key) LIKE 'batch.%'
             ORDER BY t.slug
            """
        )
    ).all()
    if colliding:
        slugs = ", ".join(row.slug for row in colliding)
        # Raise as a Python exception so Alembic rolls back cleanly.
        # The wording matches the failure-mode contract in the module
        # docstring (and the locked policy in the Sprint 50 roadmap
        # entry's Risks block).
        raise RuntimeError(
            "reserved label-key collision detected. "
            f"Tenants {{{slugs}}} hold labels under the batch.* namespace "
            "reserved by ADR 028 for Sprint 50 tag-batch grouping. "
            "See docs/runbooks/reserved-label-key-collision.md for "
            "the rename procedure. Migration refused — re-run after "
            "reconciliation."
        )

    # -- 2. Extend entity_type CHECK constraint to allow 'tag' --
    # CHECK constraints are not in-place mutable; drop + recreate.
    op.drop_constraint("ck_labels_entity_type", "labels", type_="check")
    op.create_check_constraint(
        "ck_labels_entity_type",
        "labels",
        "entity_type IN (" + ", ".join(f"'{value}'" for value in _ENTITY_TYPES_V2) + ")",
    )

    # -- 3. Backfill reserved labels for every existing tenant --
    # ON CONFLICT covers the edge case where this migration is partly
    # re-applied (e.g. after a downgrade that skipped step 3).
    # uq_labels_tenant_type_lower_key is a functional index on
    # lower(key), not a constraint, so we INSERT … WHERE NOT EXISTS
    # rather than ON CONFLICT (functional indexes can't be conflict
    # targets without an explicit constraint name).
    for key in _RESERVED_BATCH_KEYS:
        # Explicit bindparam type — :key appears as both a VARCHAR
        # column value and inside lower(:key) (TEXT-returning). Without
        # an explicit type, asyncpg's prepare deduces conflicting
        # types and fails with AmbiguousParameterError ("text versus
        # character varying"). SQLAlchemy psycopg2 path tolerates this
        # by passing parameters server-side; asyncpg does not.
        bind.execute(
            sa.text(
                """
                INSERT INTO labels (tenant_id, entity_type, key, created_at, updated_at)
                SELECT t.id, 'tag', :key, now(), now()
                  FROM tenants t
                 WHERE NOT EXISTS (
                     SELECT 1 FROM labels l
                      WHERE l.tenant_id = t.id
                        AND l.entity_type = 'tag'
                        AND lower(l.key) = lower(:key)
                 )
                """
            ).bindparams(sa.bindparam("key", value=key, type_=sa.Text())),
        )


def downgrade() -> None:
    bind = op.get_bind()

    # -- 3 reverse: delete reserved label rows (only the ones we
    # inserted with no associations; if an operator manually bound
    # one to a tag entity, we leave it to avoid silent data loss).
    bind.execute(
        sa.text(
            """
            DELETE FROM labels l
             WHERE l.entity_type = 'tag'
               AND (lower(l.key) = 'batch' OR lower(l.key) LIKE 'batch.%')
               AND NOT EXISTS (
                   SELECT 1 FROM entity_labels el WHERE el.label_id = l.id
               )
            """
        )
    )

    # -- 2 reverse: revert CHECK constraint to the migration-039 set.
    # If any 'tag'-typed labels still exist (because they had
    # associations and were preserved above), this DROP+ADD will fail
    # with a CHECK violation — surface that as a clear error rather
    # than silently leaving the schema inconsistent.
    op.drop_constraint("ck_labels_entity_type", "labels", type_="check")
    op.create_check_constraint(
        "ck_labels_entity_type",
        "labels",
        "entity_type IN ('asset', 'site', 'zone', 'device', 'category')",
    )
