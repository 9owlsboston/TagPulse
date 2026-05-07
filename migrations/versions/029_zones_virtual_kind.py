"""Sprint 17 — allow ``zones.kind = 'virtual'``.

Virtual zones are admin-defined logical groupings (no readers, no polygon) used
for cross-cutting categories like ``Cold-chain``, ``FDA-controlled``, or
``Critical assets``. They had been documented in the user guide but rejected by
both the Pydantic ``Literal`` and the ``ck_zones_kind_payload`` CHECK
constraint. This migration relaxes the CHECK to add a third branch:

    kind = 'virtual'
      AND fixed_reader_ids IS NULL
      AND polygon_geojson  IS NULL

The corresponding Pydantic ``ZoneCreate.kind`` Literal is updated in the same
sprint so 422s come from the schema layer first.

Revision ID: 029
Revises: 028
Create Date: 2026-05-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE zones DROP CONSTRAINT ck_zones_kind_payload")
    op.execute(
        "ALTER TABLE zones ADD CONSTRAINT ck_zones_kind_payload CHECK ("
        "(kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL "
        " AND jsonb_array_length(fixed_reader_ids) > 0)"
        " OR (kind = 'geofence' AND polygon_geojson IS NOT NULL)"
        " OR (kind = 'virtual' AND fixed_reader_ids IS NULL "
        "     AND polygon_geojson IS NULL))"
    )


def downgrade() -> None:
    # Reject downgrade if any virtual zones exist — silently dropping the rows
    # would lose admin-curated groupings; refuse and let the operator decide.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM zones WHERE kind = 'virtual') THEN "
        "RAISE EXCEPTION 'Cannot downgrade: virtual zones exist. "
        "Delete or convert them first.'; "
        "END IF; END $$;"
    )
    op.execute("ALTER TABLE zones DROP CONSTRAINT ck_zones_kind_payload")
    op.execute(
        "ALTER TABLE zones ADD CONSTRAINT ck_zones_kind_payload CHECK ("
        "(kind = 'reader_bound' AND fixed_reader_ids IS NOT NULL "
        " AND jsonb_array_length(fixed_reader_ids) > 0)"
        " OR (kind = 'geofence' AND polygon_geojson IS NOT NULL))"
    )
