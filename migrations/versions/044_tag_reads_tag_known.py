"""Sprint 50 Phase A2: tag_reads.tag_known soft-gating column.

Revision ID: 044
Revises: 043
Create Date: 2026-05-23

Implements [ADR-028](../../docs/adr/028-tags-as-first-class-entity.md)
§"Gating: tag_known on tag_reads".
Adds a nullable boolean to ``tag_reads`` that the registrar worker
populates asynchronously to denote whether the read corresponds to a
tag the tenant owns. Three-valued:

- ``NULL`` — registrar worker has not yet processed this row. UX
  copy assumes operators effectively never see this once the SLI
  (Sprint 50 risks: p95 < 10 s) holds.
- ``TRUE`` — EPC was in ``tags`` with ``status IN ('registered',
  'active')`` when the worker checked.
- ``FALSE`` — EPC was not in ``tags`` or its status was terminal
  (``retired`` / ``defective`` / ``transferred_out``).

**Critical hot-path constraint (ADR 028 §"Hot-path interaction"):**
the MQTT ingest path must NOT populate this column. The default is
NULL precisely so ingest can stay an oblivious append. Only the
registrar worker (Phase D) is allowed to write this column. There is
no DB-level enforcement of that — it's a service-layer contract,
identical to how ``tag_presence`` is written only by the v2 reconciler.

No index. The dominant query for this column is the registrar worker's
own ``WHERE tag_known IS NULL`` scan, and that lives on the recent
chunk(s) of the hypertable where a partial index would just slow
inserts; the worker drains a small backlog and converges, it doesn't
sweep history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "044"
down_revision: str | None = "043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tag_reads",
        sa.Column("tag_known", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tag_reads", "tag_known")
