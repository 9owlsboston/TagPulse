"""Sprint 41 Phase A: extend ``rules`` for Configurable Signaling Events.

Revision ID: 040
Revises: 039
Create Date: 2026-05-18

Implements §"Schema" of [ADR-021 v2 Configurable Signaling Events](../../docs/adr/021-configurable-sensing-events.md).

Adds 9 additive columns to ``rules`` (all nullable or defaulted so
existing rows survive untouched; NULL ``event_type`` is the implicit
``kind = legacy`` discriminator the API + UI use to keep the new
"Signaling Events" namespace separate from pre-existing rules):

- ``event_type VARCHAR(32) NULL`` —
  ``location`` / ``geolocation`` / ``temperature`` / ``geofencing``.
- ``trigger VARCHAR(32) NULL`` —
  ``on_change`` / ``periodic`` / ``on_inactivity`` / ``on_inference`` /
  ``on_entry`` / ``on_exit``. (``trigger`` is a non-reserved keyword in
  PostgreSQL so it does not need quoting; matches the ADR verbatim.)
- ``processor VARCHAR(32) NULL`` —
  ``isolated_zones`` / ``overlapping_zones``.
- ``confidence_threshold NUMERIC(3, 2) NOT NULL DEFAULT 0.0`` —
  ``0.0`` (All) / ``0.5`` / ``0.75``. Defaulted so the column is
  retroactively usable on legacy rules.
- ``category_ids UUID[] NOT NULL DEFAULT '{}'`` — empty array means
  "applies to all categories" (legacy semantics).
- ``asset_label_filters JSONB NULL`` / ``zone_label_filters JSONB NULL``
  / ``site_label_filters JSONB NULL`` —
  list of ``{key, value_in: [...]}`` AND-ed together. Per ADR 020,
  filters are evaluated as ``ANY(value_in) AND ALL(other_keys)``.
- ``integration_ids UUID[] NULL`` — per-rule outbound routing. NULL or
  empty array means "broadcast to all tenant integrations" (legacy
  behaviour); a populated array replaces the broadcast.

Plus the partial index that keeps the new signaling-events evaluator's
hot path narrow (only currently-enabled signaling rules show up):

- ``idx_rules_signaling_active ON rules (tenant_id, event_type, trigger)
  WHERE enabled = true AND event_type IS NOT NULL``

The 12 new ``condition_type`` values
(``signaling.<event_type>.<trigger>``) plus the Pydantic
discriminated-union validation live in
``src/tagpulse/models/rule_schemas.py`` — see Phase A3 / A4 in the
sprint plan ([docs/roadmap.md](../../docs/roadmap.md)) for the
breakdown.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- 1. Signaling taxonomy (nullable for legacy rules) --
    op.add_column("rules", sa.Column("event_type", sa.String(32), nullable=True))
    op.add_column("rules", sa.Column("trigger", sa.String(32), nullable=True))
    op.add_column("rules", sa.Column("processor", sa.String(32), nullable=True))

    # -- 2. Confidence + scoping (NOT NULL with defaults so legacy rows
    #       get safe values; the columns are also useful retroactively
    #       on legacy rules) --
    op.add_column(
        "rules",
        sa.Column(
            "confidence_threshold",
            sa.Numeric(3, 2),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "rules",
        sa.Column(
            "category_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )
    op.add_column("rules", sa.Column("asset_label_filters", JSONB(), nullable=True))
    op.add_column("rules", sa.Column("zone_label_filters", JSONB(), nullable=True))
    op.add_column("rules", sa.Column("site_label_filters", JSONB(), nullable=True))

    # -- 3. Per-rule integration routing (replaces global broadcast
    #       when populated; NULL / empty = legacy broadcast) --
    op.add_column(
        "rules",
        sa.Column("integration_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
    )

    # -- 4. Partial index for the signaling-events evaluator hot path --
    op.execute(
        "CREATE INDEX idx_rules_signaling_active "
        "ON rules (tenant_id, event_type, trigger) "
        "WHERE enabled = true AND event_type IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_rules_signaling_active")
    op.drop_column("rules", "integration_ids")
    op.drop_column("rules", "site_label_filters")
    op.drop_column("rules", "zone_label_filters")
    op.drop_column("rules", "asset_label_filters")
    op.drop_column("rules", "category_ids")
    op.drop_column("rules", "confidence_threshold")
    op.drop_column("rules", "processor")
    op.drop_column("rules", "trigger")
    op.drop_column("rules", "event_type")
