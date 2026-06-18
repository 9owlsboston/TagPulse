"""Device→site assignment (Sprint 64 follow-up) — ``devices.site_id``.

Adds a nullable ``site_id`` FK on ``devices`` so a fixed reader can be assigned
to the site/floor it physically lives on. This is the keystone the **accurate
floor-polygon zone resolver** needs: to test an antenna's ``(x, y)`` against the
right floor's polygons it must know which floor the device is on, and the only
device→site link before this was circular (through the very ``reader_bound`` zone
the floor path replaces). See
[device-site-assignment-and-floor-zones.md](../../docs/design/device-site-assignment-and-floor-zones.md).

Additive — changes nothing about today's ``reader_bound`` resolution:

- ``devices.site_id``  new nullable FK → ``sites(id)``, ``ON DELETE SET NULL``
  (deleting a site un-assigns its readers rather than cascading them away).
- index ``ix_devices_site_id``.
- **Backfill**: a *fixed* reader that is listed in **exactly one**
  ``reader_bound`` zone inherits that zone's ``site_id``. Ambiguous (0 or >1)
  stay ``NULL`` for an operator to assign.

Revision ID: 055
Revises: 054
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic
revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("site_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_devices_site_id",
        "devices",
        "sites",
        ["site_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_devices_site_id", "devices", ["site_id"])

    # Backfill: fixed readers in exactly one reader_bound zone inherit its site.
    op.execute(
        """
        UPDATE devices d
        SET site_id = z.site_id
        FROM (
            SELECT (jsonb_array_elements_text(fixed_reader_ids))::uuid AS device_id,
                   site_id
            FROM zones
            WHERE kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL
        ) z
        WHERE d.id = z.device_id
          AND d.mobility = 'fixed'
          AND d.site_id IS NULL
          AND (
            SELECT count(*) FROM zones z2
            WHERE z2.kind = 'reader_bound'
              AND z2.fixed_reader_ids @> to_jsonb(d.id::text)
          ) = 1
        """
    )


def downgrade() -> None:
    op.drop_index("ix_devices_site_id", table_name="devices")
    op.drop_constraint("fk_devices_site_id", "devices", type_="foreignkey")
    op.drop_column("devices", "site_id")
