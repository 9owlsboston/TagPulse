"""Add FK constraint from tag_reads.device_id to devices.id.

Revision ID: 003
Revises: 002
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_tag_reads_device_id",
        "tag_reads",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tag_reads_device_id", "tag_reads", type_="foreignkey")
