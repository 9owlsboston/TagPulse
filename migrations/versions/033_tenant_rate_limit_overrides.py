"""Sprint 22 A4: per-tenant rate-limit overrides.

Revision ID: 033
Revises: 032
Create Date: 2026-05-06

Adds ``tenants.rate_limit_overrides JSONB`` so operators can lift or
lower the global per-route-class limits set in ``Settings`` for a
specific tenant without redeploying the API. Shape::

    {"ingest": 12000, "read": 1200, "write": 600, "admin": 240}

Any subset of keys is allowed; missing keys fall back to the global
default. NULL means "no overrides" (the common case).

Per [ADR-016 §5](../../docs/adr/016-multi-cloud-deployment-strategy.md).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("rate_limit_overrides", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "rate_limit_overrides")
