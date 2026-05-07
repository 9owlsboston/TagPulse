"""Sprint 16 — per-device rotatable tokens (A6 Phase 1).

Adds ``token_hash`` / ``token_prefix`` / ``token_rotated_at`` columns to the
``devices`` table so each device can present a Bearer token that is hashed
at rest and rotated atomically by an admin via
``POST /device-registry/{id}/rotate-token``.

Per [docs/design/edge-device-contract.md §5.2](../../docs/design/edge-device-contract.md)
and [ADR-011 Phase 1](../../docs/adr/011-device-identity-roadmap.md).

Revision ID: 025
Revises: 024
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("token_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("token_prefix", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Prefix index supports the future Bearer-token lookup path
    # (``WHERE token_prefix = $1`` then SHA-256 compare).
    op.create_index(
        "ix_devices_token_prefix",
        "devices",
        ["token_prefix"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_devices_token_prefix", table_name="devices")
    op.drop_column("devices", "token_rotated_at")
    op.drop_column("devices", "token_prefix")
    op.drop_column("devices", "token_hash")
