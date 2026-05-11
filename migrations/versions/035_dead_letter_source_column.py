"""Sprint 28 C3: add source column to dead_letter_events.

Revision ID: 035
Revises: 034
Create Date: 2026-05-09

Today every row in ``dead_letter_events`` looks alike — operators must
parse ``topic`` to tell whether a row was emitted by the AsyncEventBus
(handler crashed), the ingestion clock-window guard
(``tag_read.rejected_clock``), or the MQTT subscriber (Sprint 28 C3).
Adding a low-cardinality ``source`` enum lets the dead-letter triage
runbook (Sprint 28 E3) and the admin UI route the row to the right
investigator without string-matching topic prefixes.

Allowed values (enforced as a CHECK constraint, not a DB enum, so adding
new sources doesn't require a migration cycle on existing rows):
``event_bus`` | ``tag_read_rejected`` | ``mqtt_subscriber`` | ``other``.

Existing rows are backfilled with ``'event_bus'`` (the historical
default writer).
"""

import sqlalchemy as sa
from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

ALLOWED_SOURCES = ("event_bus", "tag_read_rejected", "mqtt_subscriber", "other")


def upgrade() -> None:
    op.add_column(
        "dead_letter_events",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'event_bus'"),
        ),
    )
    op.create_check_constraint(
        "ck_dead_letter_events_source",
        "dead_letter_events",
        f"source IN ({', '.join(repr(s) for s in ALLOWED_SOURCES)})",
    )
    op.create_index(
        "ix_dead_letter_events_source_failed_at",
        "dead_letter_events",
        ["source", "failed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dead_letter_events_source_failed_at", table_name="dead_letter_events")
    op.drop_constraint(
        "ck_dead_letter_events_source",
        "dead_letter_events",
        type_="check",
    )
    op.drop_column("dead_letter_events", "source")
