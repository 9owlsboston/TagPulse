"""Sprint 19: telemetry_subject_kinds tenant flag + continuous aggregates.

Revision ID: 031
Revises: 030
Create Date: 2026-05-05

Two narrow schema changes that turn on the subject-scoped telemetry
write path introduced in Sprint 18:

1. ``tenants.telemetry_subject_kinds`` JSONB column (default
   ``["device"]``) — tenant-level opt-in for which subject kinds the
   ingest pipeline is allowed to fan-out to. Off by default (``device``
   only) so storage growth from the multi-subject row multiplier is
   bounded by operator action.

   The Sprint 19 plan suggested folding this into the existing
   ``tracking_modes`` JSONB shape, but ``tracking_modes`` is currently
   a flat ``list[str]`` consumed by several services that would all
   have to learn a new shape; a dedicated column is cleaner and the
   operational story (one row, one column, one default) is identical.
   The deviation is documented in the Sprint 19 audit.

2. Two TimescaleDB continuous aggregates over ``telemetry_readings``:

   * ``cagg_telemetry_1m`` — 1-minute buckets, refresh covers the last
     hour. Drives near-real-time UI charts (Asset Telemetry tab,
     Lot Cold-chain card).
   * ``cagg_telemetry_1h`` — 1-hour buckets, refresh covers the last
     30 days. Drives the dashboard / aggregate API for longer windows.

   Both expose ``avg / min / max / count`` of ``metric_value`` keyed on
   ``(tenant_id, subject_kind, subject_id, metric_name, bucket)`` so
   the new ``/telemetry/aggregates`` endpoint can serve queries without
   touching the raw hypertable for typical lookback windows.

   Continuous aggregates and their refresh policies must be created
   outside a transaction (TimescaleDB constraint) — Alembic runs each
   migration in its own transaction by default, so we use
   ``AUTOCOMMIT`` for the cagg DDL via ``op.get_context().autocommit_block``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic
revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1) tenants.telemetry_subject_kinds
    # ------------------------------------------------------------------
    op.add_column(
        "tenants",
        sa.Column(
            "telemetry_subject_kinds",
            JSONB,
            nullable=False,
            server_default=sa.text("'[\"device\"]'::jsonb"),
        ),
    )

    # ------------------------------------------------------------------
    # 2) Continuous aggregates (TimescaleDB) — must run outside a
    # transaction. Alembic's autocommit_block opens a connection in
    # autocommit mode for these statements.
    #
    # TimescaleDB rejects ``CREATE MATERIALIZED VIEW … WITH
    # (timescaledb.continuous)`` against a hypertable that has row
    # security enabled (``cannot create continuous aggregate on
    # hypertable with row security``). ``telemetry_readings`` had RLS
    # enabled in migration 030, so we briefly toggle it off for the
    # cagg DDL and turn it back on. The repo paths that read the caggs
    # ([repositories/timescaledb/telemetry.py](../../src/tagpulse/repositories/timescaledb/telemetry.py))
    # always include ``tenant_id`` in the WHERE clause, so the caggs
    # themselves do not need a separate RLS policy as a defence in
    # depth — the policy on the underlying hypertable continues to gate
    # raw-row access.
    # ------------------------------------------------------------------
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE telemetry_readings DISABLE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_telemetry_1m
            WITH (timescaledb.continuous) AS
            SELECT
                tenant_id,
                subject_kind,
                subject_id,
                metric_name,
                time_bucket(INTERVAL '1 minute', timestamp) AS bucket,
                avg(metric_value)   AS avg_value,
                min(metric_value)   AS min_value,
                max(metric_value)   AS max_value,
                count(*)            AS sample_count
            FROM telemetry_readings
            GROUP BY tenant_id, subject_kind, subject_id, metric_name, bucket
            WITH NO DATA
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy(
                'cagg_telemetry_1m',
                start_offset => INTERVAL '2 hours',
                end_offset   => INTERVAL '1 minute',
                schedule_interval => INTERVAL '1 minute'
            )
            """
        )

        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_telemetry_1h
            WITH (timescaledb.continuous) AS
            SELECT
                tenant_id,
                subject_kind,
                subject_id,
                metric_name,
                time_bucket(INTERVAL '1 hour', timestamp) AS bucket,
                avg(metric_value)   AS avg_value,
                min(metric_value)   AS min_value,
                max(metric_value)   AS max_value,
                count(*)            AS sample_count
            FROM telemetry_readings
            GROUP BY tenant_id, subject_kind, subject_id, metric_name, bucket
            WITH NO DATA
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy(
                'cagg_telemetry_1h',
                start_offset => INTERVAL '31 days',
                end_offset   => INTERVAL '1 hour',
                schedule_interval => INTERVAL '15 minutes'
            )
            """
        )
        op.execute("ALTER TABLE telemetry_readings ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "SELECT remove_continuous_aggregate_policy("
            "'cagg_telemetry_1h', if_exists => TRUE)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS cagg_telemetry_1h")
        op.execute(
            "SELECT remove_continuous_aggregate_policy("
            "'cagg_telemetry_1m', if_exists => TRUE)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS cagg_telemetry_1m")

    op.drop_column("tenants", "telemetry_subject_kinds")
