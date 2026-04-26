"""Add RLS policy on analytics_results table.

Revision ID: 009
Revises: 008
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE analytics_results ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_analytics_results ON analytics_results "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_analytics_results "
        "ON analytics_results"
    )
    op.execute("ALTER TABLE analytics_results DISABLE ROW LEVEL SECURITY")
