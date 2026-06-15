"""Branding logo upload (chore) — widen ``logo_url`` + add ``logo_collapsed_url``.

Lets an admin upload **two** logo images (a full/expanded wordmark for the
240px sidebar header and a square mark for the 64px collapsed rail) without
standing up blob storage: the images are stored as ``data:`` URLs in the
existing branding columns. The column type is therefore widened from
``VARCHAR(2048)`` (only fits an ``https://`` URL) to ``TEXT`` so it can hold a
small base64 ``data:`` URL, and a sibling ``logo_collapsed_url`` column is added
for the second image. Both stay ``NULL`` = use the system default.

Deliberately **no blob storage / upload endpoint** — for brand logos (small,
square-ish, changed rarely) a size-capped ``data:`` URL on the branding row is
the pragmatic store: zero new infra, reuses the existing ``PATCH
/tenant/branding`` write path. The column stays a *string*, so a future
migration to operator-hosted ``https://`` URLs (or blob) is non-breaking — same
field, different value. The byte-size cap is enforced at the API layer
(Pydantic), not the DB, mirroring how the other branding fields validate.

Two additive/widening changes, no behavioural change to anything that exists:

- ``tenants.logo_url``            ``VARCHAR(2048)`` → ``TEXT`` (holds a data URL).
- ``tenants.logo_collapsed_url``  new ``TEXT`` column, ``NULL`` = no second logo.

Revision ID: 054
Revises: 053
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Widen logo_url so it can hold a small base64 data: URL (not just an
    # https:// URL that fit in 2048 chars).
    op.alter_column(
        "tenants",
        "logo_url",
        existing_type=sa.String(length=2048),
        type_=sa.Text(),
        existing_nullable=True,
    )
    # The second (collapsed-sidebar) logo.
    op.add_column(
        "tenants",
        sa.Column("logo_collapsed_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "logo_collapsed_url")
    # Narrowing back to VARCHAR(2048) would truncate any stored data: URL, so
    # the downgrade is best-effort: it restores the type but a data-URL logo
    # would need clearing first (operator responsibility).
    op.alter_column(
        "tenants",
        "logo_url",
        existing_type=sa.Text(),
        type_=sa.String(length=2048),
        existing_nullable=True,
    )
