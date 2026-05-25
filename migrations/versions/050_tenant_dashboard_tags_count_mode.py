"""Sprint 54 follow-up: per-tenant dashboard tag-counting mode.

Revision ID: 050
Revises: 049
Create Date: 2026-05-25

Adds ``tenants.dashboard_tags_count_mode`` (VARCHAR(16) NOT NULL
DEFAULT ``'live'``) so the UI's new Tags KPI tile can be tuned per
tenant without code changes. Three modes, mirroring the predicate
sets already in the dashboard / reconciliation services:

- ``all``           — every row in ``tags`` for the tenant.
- ``live``          — ``status IN ('registered', 'active')`` (default,
                      matches ``_LIVE_TAG_STATUSES`` in
                      :mod:`tagpulse.services.dashboard`).
- ``non_terminal``  — ``status NOT IN ('retired', 'defective',
                      'transferred_out')`` (matches
                      ``_TERMINAL_TAG_STATUSES`` complement).

A CHECK constraint pins the enum at the DB layer; Pydantic enforces it
at the route layer. Joins the existing per-tenant integer / JSON knobs
from Sprint 22 / 50 / 54.3 — same pattern, same upgrade path. Existing
tenants backfill via the server_default.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "dashboard_tags_count_mode",
            sa.String(length=16),
            nullable=False,
            server_default="live",
        ),
    )
    op.create_check_constraint(
        "ck_tenants_dashboard_tags_count_mode",
        "tenants",
        "dashboard_tags_count_mode IN ('all', 'live', 'non_terminal')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tenants_dashboard_tags_count_mode", "tenants", type_="check"
    )
    op.drop_column("tenants", "dashboard_tags_count_mode")
