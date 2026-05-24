"""Sprint 50 Phase C1: tenants.tag_bulk_import_rate_limit configurable cap.

Revision ID: 046
Revises: 045
Create Date: 2026-05-23

Implements [ADR-028 OQ 4 resolution](../../docs/adr/028-tags-as-first-class-entity.md):
the per-tenant hourly cap on ``POST /tags/import`` calls. Default is
10 / hour per tenant (matches ADR 028 §"OQ 4"). Operators with a
documented batch-onboarding flow can lift it via
``PATCH /tenant-config``; the long-term default stays generous-but-not-runaway.

This is **not** a route_class on the existing
:mod:`tagpulse.core.rate_limit` per-minute limiter — that one is
minute-scale and route-class-keyed. ``tag_bulk_import_rate_limit``
is a per-hour, per-tenant, per-endpoint counter (one specific
endpoint) and lives in :mod:`tagpulse.core.tag_import_rate_limit`.
Encoding it as a tenants column (vs. a global setting) keeps the
override path consistent with the rest of the per-tenant knobs
(``rate_limit_overrides``, ``tag_bulk_two_person_threshold`` —
Phase C3, ``tag_bulk_import_rate_limit`` — this migration).

The value is an ``INT NOT NULL DEFAULT 10`` so existing tenants
backfill to the ADR default without any operator action.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "tag_bulk_import_rate_limit",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "tag_bulk_import_rate_limit")
