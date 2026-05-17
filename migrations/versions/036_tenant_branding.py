"""Sprint 33 QW6: per-tenant branding (logo + display name + brand colour).

Revision ID: 036
Revises: 035
Create Date: 2026-05-17

Adds three nullable columns to ``tenants`` so operators can override the
default brand chrome (logo, display name, primary colour) per tenant
without forking the UI. Scoped per the QW6 row in
[docs/design/reference-design-remediation.md §3.3](../../docs/design/reference-design-remediation.md#33-additions-surfaced-during-planning-not-in-the-original-audits):

- ``logo_url VARCHAR(2048) NULL``     — HTTPS URL the operator hosts
                                        (CDN, blob storage, public site).
                                        NULL → UI falls back to the
                                        default TagPulse logo asset.
- ``display_name VARCHAR(255) NULL``  — Friendly name shown in the Sider
                                        header / login page in place of
                                        ``tenants.name``. NULL → UI uses
                                        ``name``.
- ``brand_color VARCHAR(7) NULL``     — Hex string matching ``#RRGGBB``;
                                        overrides the default teal
                                        ``colorPrimary`` at the
                                        ``ConfigProvider`` level. NULL →
                                        UI uses the algorithm default.

No secret material involved — logos are public-by-design URLs and the
brand colour is cosmetic. All three columns are nullable so existing
tenants migrate without touching their rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("logo_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("brand_color", sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "brand_color")
    op.drop_column("tenants", "display_name")
    op.drop_column("tenants", "logo_url")
