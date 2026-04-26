"""Add RLS policy on dead_letter_events table.

Revision ID: 013
Revises: 012
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic
revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE dead_letter_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_dead_letter_events "
        "ON dead_letter_events "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_dead_letter_events "
        "ON dead_letter_events"
    )
    op.execute("ALTER TABLE dead_letter_events DISABLE ROW LEVEL SECURITY")
