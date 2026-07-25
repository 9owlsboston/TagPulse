"""Sprint 80 — generalize external_locations to all subject_kinds (I-9HQA).

Adds ``subject_kind`` / ``subject_id`` so a non-asset subject (starting with a
gateway's own ``device``) can carry an external position, additively — asset
rows keep their ``asset_id`` and every existing asset query/index is unchanged.

Expand-phase safe: the new columns are **nullable** (migrations run pre-rollout
while old API code is still live, so a NOT NULL column the old writers omit
would break every legacy insert). New writers always set them; a later contract
migration can add NOT NULL once all writers do.

See docs/design/external-locations-subject-kinds.md.

Revision ID: 060
Revises: 059
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "external_locations",
        sa.Column("subject_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "external_locations",
        sa.Column("subject_id", UUID(as_uuid=True), nullable=True),
    )
    # Backfill existing rows — every current row is an asset position.
    op.execute(
        "UPDATE external_locations "
        "SET subject_kind = 'asset', subject_id = asset_id "
        "WHERE subject_kind IS NULL"
    )
    # asset_id is now optional (non-asset subjects have no asset).
    op.alter_column("external_locations", "asset_id", nullable=True)
    op.create_index(
        "ix_external_locations_by_subject",
        "external_locations",
        ["tenant_id", "subject_kind", "subject_id", sa.text("recorded_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_locations_by_subject", table_name="external_locations"
    )
    # Non-asset rows cannot satisfy the restored asset_id NOT NULL (subject_id
    # is not a valid asset FK), so drop them. Data-lossy by design — a rollback
    # discards positions recorded for non-asset subjects. No-op on empty CI DB.
    op.execute("DELETE FROM external_locations WHERE asset_id IS NULL")
    op.alter_column("external_locations", "asset_id", nullable=False)
    op.drop_column("external_locations", "subject_id")
    op.drop_column("external_locations", "subject_kind")
