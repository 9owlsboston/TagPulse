"""Sprint 82 — asset display_label + 'vin' binding kind (I-P923).

TagPulse-Mobile needs to (1) Map-link a scanned VIN to a vehicle asset and
(2) show the vehicle's license plate. Adds a nullable ``assets.display_label``
(the plate; generic secondary human label) and a new ``'vin'`` binding kind so
a VIN can be bound as a pure lookup handle — distinct from ``'device'``, which
the telemetry-association SQL interprets as ``tr.tag_id = binding_value``.

Additive + expand-safe: ``display_label`` is nullable; the CHECK is only
widened (existing rows always satisfy the superset).

See docs/design/asset-display-label-vin-lookup.md.

Revision ID: 062
Revises: 061
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("display_label", sa.String(length=255), nullable=True),
    )
    # Widen the binding-kind CHECK to admit 'vin'.
    op.drop_constraint("ck_asset_tag_bindings_kind", "asset_tag_bindings", type_="check")
    op.create_check_constraint(
        "ck_asset_tag_bindings_kind",
        "asset_tag_bindings",
        "binding_kind IN ('epc','tid','device','vin')",
    )


def downgrade() -> None:
    # Narrowing the CHECK requires no 'vin' rows remain; drop them (data-lossy
    # by design — a rollback discards VIN bindings). No-op on the empty CI DB.
    op.execute("DELETE FROM asset_tag_bindings WHERE binding_kind = 'vin'")
    op.drop_constraint("ck_asset_tag_bindings_kind", "asset_tag_bindings", type_="check")
    op.create_check_constraint(
        "ck_asset_tag_bindings_kind",
        "asset_tag_bindings",
        "binding_kind IN ('epc','tid','device')",
    )
    op.drop_column("assets", "display_label")
