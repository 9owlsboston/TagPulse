"""Subject-scoped telemetry: schema + back-compat (Sprint 18).

Revision ID: 030
Revises: 029
Create Date: 2026-05-05

Introduces a subject-scoped ``telemetry_readings`` hypertable keyed on
``(tenant_id, subject_kind, subject_id)`` and re-routes the existing
device-scoped telemetry path through it without changing observable
behaviour.

Strategy (per docs/design/subject-scoped-telemetry.md §3 and ADR-013):

1. Create the new ``telemetry_readings`` hypertable + RLS + indexes.
2. Rename ``device_telemetry`` -> ``telemetry_readings_legacy_device``.
3. Back-fill every legacy row into ``telemetry_readings`` with
   ``subject_kind='device'``, ``subject_id=device_id``, ``source='device'``.
4. Re-create ``device_telemetry`` as a read-only SQL view selecting from
   ``telemetry_readings`` where ``subject_kind='device'`` so external
   consumers (Grafana dashboards, ad-hoc psql queries) keep working
   through the deprecation window.
5. Extend ``telemetry_models`` with a ``subject_kind`` column (default
   ``'device'`` for back-compat) and replace the
   ``UNIQUE(tenant_id, device_type)`` constraint with
   ``UNIQUE(tenant_id, subject_kind, COALESCE(device_type, ''))``.
6. Extend ``telemetry_quarantine`` with nullable ``subject_kind`` and
   ``subject_id`` columns so multi-subject ingest in Sprint 19 can
   record richer context without a follow-up migration.

The application's repository layer is updated in the same commit to
write to ``telemetry_readings`` directly (with ``subject_kind='device'``);
the back-compat view is read-only.

``downgrade()`` reverses every step in strict LIFO order; the back-fill
is reversed by copying ``subject_kind='device'`` rows back into the
renamed table before dropping the new schema. CI exercises the round-
trip on a populated database.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic
revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# All allowed subject kinds. Kept in sync with the Pydantic enum
# ``SubjectKind`` in ``tagpulse.models.schemas`` and with the
# ``ck_telemetry_readings_subject_kind`` constraint defined below.
_SUBJECT_KINDS = ("device", "asset", "lot", "stock_item", "zone")
_SUBJECT_KINDS_SQL = ", ".join(f"'{k}'" for k in _SUBJECT_KINDS)

# Allowed source vocabularies (see docs/design/rfid-tag-data-model.md §D4).
_SOURCES = ("device", "tag", "external", "derived")
_SOURCES_SQL = ", ".join(f"'{s}'" for s in _SOURCES)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1) Create the new telemetry_readings hypertable.
    # ------------------------------------------------------------------
    op.create_table(
        "telemetry_readings",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        # device_id is the *reporting* device when one is known; always
        # populated for source='device' rows and usually populated for
        # source='tag' rows (the reader that saw the tag).
        sa.Column("device_id", UUID(as_uuid=True), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'device'"),
        ),
        sa.Column("metadata", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", "timestamp"),
        sa.CheckConstraint(
            f"subject_kind IN ({_SUBJECT_KINDS_SQL})",
            name="ck_telemetry_readings_subject_kind",
        ),
        sa.CheckConstraint(
            f"source IN ({_SOURCES_SQL})",
            name="ck_telemetry_readings_source",
        ),
    )

    op.execute(
        "SELECT create_hypertable('telemetry_readings', 'timestamp', "
        "if_not_exists => TRUE)"
    )

    # Hot path: per-subject metric history.
    op.create_index(
        "ix_telemetry_readings_subject",
        "telemetry_readings",
        [
            "tenant_id",
            "subject_kind",
            "subject_id",
            "metric_name",
            sa.text("timestamp DESC"),
        ],
    )
    # Reporting-device lookup (replaces ix_device_telemetry_lookup for
    # source='device' rows; also serves rules that want "all readings a
    # given device emitted regardless of subject").
    op.execute(
        "CREATE INDEX ix_telemetry_readings_device "
        "ON telemetry_readings (tenant_id, device_id, metric_name, "
        "timestamp DESC) WHERE device_id IS NOT NULL"
    )

    op.execute("ALTER TABLE telemetry_readings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_telemetry_readings "
        "ON telemetry_readings "
        "USING (tenant_id = current_setting('app.current_tenant_id')::uuid)"
    )

    # ------------------------------------------------------------------
    # 2) Rename the legacy table out of the way.
    #
    # Indexes and policies are kept attached by OID; their cosmetic names
    # still reference 'device_telemetry'. They will be dropped during
    # downgrade. The table will eventually be dropped in Sprint 20 once
    # the deprecation window closes.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE device_telemetry RENAME TO telemetry_readings_legacy_device"
    )

    # ------------------------------------------------------------------
    # 3) Back-fill: copy every legacy row into the new table.
    #
    # Idempotent on its own row set: re-running this migration on a
    # populated DB requires a clean state, but the DML itself uses
    # ON CONFLICT DO NOTHING to be defensive against partial replays.
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO telemetry_readings (
            id, tenant_id, subject_kind, subject_id, device_id,
            timestamp, metric_name, metric_value, unit, source, metadata
        )
        SELECT
            id,
            tenant_id,
            'device'   AS subject_kind,
            device_id  AS subject_id,
            device_id,
            timestamp,
            metric_name,
            metric_value,
            unit,
            'device'   AS source,
            metadata
        FROM telemetry_readings_legacy_device
        ON CONFLICT (id, timestamp) DO NOTHING
        """
    )

    # Row-count parity assertion (Sprint 18 acceptance criterion).
    # Counts the device-scoped rows we just inserted against the legacy
    # source. Bails out of the migration loudly if the back-fill missed
    # rows — better to fail here than to silently lose telemetry.
    op.execute(
        """
        DO $$
        DECLARE
            legacy_count   bigint;
            migrated_count bigint;
        BEGIN
            SELECT count(*) INTO legacy_count
              FROM telemetry_readings_legacy_device;
            SELECT count(*) INTO migrated_count
              FROM telemetry_readings WHERE subject_kind = 'device';
            IF legacy_count <> migrated_count THEN
                RAISE EXCEPTION
                    'Sprint 18 back-fill row-count mismatch: '
                    'legacy=%, migrated=%',
                    legacy_count, migrated_count;
            END IF;
        END
        $$;
        """
    )

    # ------------------------------------------------------------------
    # 4) Re-create the device_telemetry name as a back-compat view.
    #
    # Read-only by design: external consumers (Grafana, ad-hoc psql) can
    # keep their queries; the application writes to telemetry_readings
    # directly through the new repository.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW device_telemetry AS
        SELECT
            id,
            tenant_id,
            device_id,
            timestamp,
            metric_name,
            metric_value,
            unit,
            metadata
        FROM telemetry_readings
        WHERE subject_kind = 'device'
        """
    )

    # ------------------------------------------------------------------
    # 5) Extend telemetry_models with subject_kind.
    # ------------------------------------------------------------------
    op.add_column(
        "telemetry_models",
        sa.Column(
            "subject_kind",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'device'"),
        ),
    )
    op.create_check_constraint(
        "ck_telemetry_models_subject_kind",
        "telemetry_models",
        f"subject_kind IN ({_SUBJECT_KINDS_SQL})",
    )
    # device_type is required only when subject_kind='device' — relax
    # NOT NULL and add a conditional check instead.
    op.alter_column("telemetry_models", "device_type", nullable=True)
    op.create_check_constraint(
        "ck_telemetry_models_device_type_required",
        "telemetry_models",
        "(subject_kind = 'device' AND device_type IS NOT NULL) "
        "OR (subject_kind <> 'device' AND device_type IS NULL)",
    )
    # Replace the old (tenant_id, device_type) uniqueness with one that
    # also keys on subject_kind. Old constraint name comes from migration
    # 007 (uq_telemetry_models_tenant_device_type); guard with IF EXISTS
    # so partial replays don't blow up.
    op.execute(
        "ALTER TABLE telemetry_models "
        "DROP CONSTRAINT IF EXISTS uq_telemetry_models_tenant_device_type"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_telemetry_models_tenant_device_type"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_telemetry_models_tenant_subject "
        "ON telemetry_models (tenant_id, subject_kind, "
        "COALESCE(device_type, ''))"
    )

    # ------------------------------------------------------------------
    # 6) Extend telemetry_quarantine with subject_kind / subject_id.
    #
    # Nullable on legacy rows (back-fill leaves them NULL); Sprint 19
    # ingest will populate them on every new row.
    # ------------------------------------------------------------------
    op.add_column(
        "telemetry_quarantine",
        sa.Column("subject_kind", sa.String(32), nullable=True),
    )
    op.add_column(
        "telemetry_quarantine",
        sa.Column("subject_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_telemetry_quarantine_subject_kind",
        "telemetry_quarantine",
        f"subject_kind IS NULL OR subject_kind IN ({_SUBJECT_KINDS_SQL})",
    )


def downgrade() -> None:
    # Reverse strict LIFO so the round-trip is asserted in CI.

    # 6) telemetry_quarantine extensions
    op.drop_constraint(
        "ck_telemetry_quarantine_subject_kind",
        "telemetry_quarantine",
        type_="check",
    )
    op.drop_column("telemetry_quarantine", "subject_id")
    op.drop_column("telemetry_quarantine", "subject_kind")

    # 5) telemetry_models extensions
    op.execute("DROP INDEX IF EXISTS ix_telemetry_models_tenant_subject")
    # Restore the original UNIQUE(tenant_id, device_type) constraint
    # exactly as migration 007 declared it.
    op.execute(
        "ALTER TABLE telemetry_models "
        "ADD CONSTRAINT uq_telemetry_models_tenant_device_type "
        "UNIQUE (tenant_id, device_type)"
    )
    op.drop_constraint(
        "ck_telemetry_models_device_type_required",
        "telemetry_models",
        type_="check",
    )
    op.alter_column("telemetry_models", "device_type", nullable=False)
    op.drop_constraint(
        "ck_telemetry_models_subject_kind",
        "telemetry_models",
        type_="check",
    )
    op.drop_column("telemetry_models", "subject_kind")

    # 4) Drop the view first so we can re-create the underlying table
    # under its original name.
    op.execute("DROP VIEW IF EXISTS device_telemetry")

    # 3) Reverse back-fill: re-populate the legacy table from any
    # subject_kind='device' rows in telemetry_readings that did not
    # already exist there. This restores rows that may have been
    # written by the new repository between upgrade and downgrade.
    op.execute(
        """
        INSERT INTO telemetry_readings_legacy_device (
            id, tenant_id, device_id, timestamp,
            metric_name, metric_value, unit, metadata
        )
        SELECT
            id, tenant_id, device_id, timestamp,
            metric_name, metric_value, unit, metadata
        FROM telemetry_readings
        WHERE subject_kind = 'device'
          AND device_id IS NOT NULL
        ON CONFLICT (id, timestamp) DO NOTHING
        """
    )

    # 2) Rename the legacy table back to its original name.
    op.execute(
        "ALTER TABLE telemetry_readings_legacy_device "
        "RENAME TO device_telemetry"
    )

    # 1) Drop the new table.
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_telemetry_readings "
        "ON telemetry_readings"
    )
    op.execute("ALTER TABLE telemetry_readings DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_telemetry_readings_device")
    op.drop_index(
        "ix_telemetry_readings_subject", table_name="telemetry_readings"
    )
    op.drop_constraint(
        "ck_telemetry_readings_source",
        "telemetry_readings",
        type_="check",
    )
    op.drop_constraint(
        "ck_telemetry_readings_subject_kind",
        "telemetry_readings",
        type_="check",
    )
    op.drop_table("telemetry_readings")
