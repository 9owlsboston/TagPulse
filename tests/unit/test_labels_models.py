"""Unit tests for Sprint 35 Phase A Labels (ADR 020) — ORM model shape.

Phase A is schema-only: migration 039 + the two ORM models. Pydantic
schemas, repository, routes, audit, and filter wiring all land in
Phase B and will get their own unit tests then. These tests catch
the most common Phase A regression: someone edits one of the model
classes without updating the migration (or vice versa) and the two
drift out of sync.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID

from tagpulse.models.database import EntityLabelModel, LabelModel


class TestLabelModel:
    """`labels` catalog table shape."""

    def test_tablename(self) -> None:
        assert LabelModel.__tablename__ == "labels"

    def test_columns_present(self) -> None:
        cols = set(LabelModel.__table__.columns.keys())
        expected = {
            "id",
            "tenant_id",
            "entity_type",
            "key",
            "color",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols), f"missing: {expected - cols}"

    def test_primary_key_is_id(self) -> None:
        pk_cols = [c.name for c in LabelModel.__table__.primary_key.columns]
        assert pk_cols == ["id"]

    def test_entity_type_is_short_string(self) -> None:
        col = LabelModel.__table__.columns["entity_type"]
        assert isinstance(col.type, String)
        assert col.type.length == 32
        assert col.nullable is False

    def test_key_max_length_matches_check(self) -> None:
        # CHECK constraint in migration 039 caps the key at 24 chars
        # via the regex; the column length must agree or we'd silently
        # truncate before the CHECK fires.
        col = LabelModel.__table__.columns["key"]
        assert isinstance(col.type, String)
        assert col.type.length == 24
        assert col.nullable is False

    def test_color_optional_seven_chars(self) -> None:
        col = LabelModel.__table__.columns["color"]
        assert isinstance(col.type, String)
        assert col.type.length == 7  # "#RRGGBB"
        assert col.nullable is True

    def test_tenant_id_indexed(self) -> None:
        col = LabelModel.__table__.columns["tenant_id"]
        assert col.index is True
        # FK to tenants(id) — present and non-null.
        assert col.nullable is False
        assert len(col.foreign_keys) == 1
        fk = next(iter(col.foreign_keys))
        assert fk.target_fullname == "tenants.id"

    def test_created_by_has_no_fk(self) -> None:
        # No `users` table in TagPulse — auth is JWT-based and user_id
        # is an opaque UUID claim (mirrors audit_logs.user_id, see
        # migration 015). A FK here would fail at upgrade time.
        col = LabelModel.__table__.columns["created_by"]
        assert isinstance(col.type, UUID)
        assert col.nullable is True
        assert col.foreign_keys == set()


class TestEntityLabelModel:
    """`entity_labels` association table shape."""

    def test_tablename(self) -> None:
        assert EntityLabelModel.__tablename__ == "entity_labels"

    def test_columns_present(self) -> None:
        cols = set(EntityLabelModel.__table__.columns.keys())
        expected = {"label_id", "entity_id", "value", "created_by", "created_at"}
        assert expected == cols, f"unexpected cols: {cols ^ expected}"

    def test_composite_primary_key(self) -> None:
        pk_cols = [c.name for c in EntityLabelModel.__table__.primary_key.columns]
        assert set(pk_cols) == {"label_id", "entity_id"}

    def test_label_id_fk_restrict(self) -> None:
        # The 409 + association_count delete semantics (matches the
        # Categories pattern in ADR 019) depends on ON DELETE RESTRICT
        # here. CASCADE would silently nuke associations and skip the
        # API guard.
        col = EntityLabelModel.__table__.columns["label_id"]
        assert len(col.foreign_keys) == 1
        fk = next(iter(col.foreign_keys))
        assert fk.target_fullname == "labels.id"
        assert fk.ondelete == "RESTRICT"

    def test_entity_id_polymorphic_no_fk(self) -> None:
        # entity_id points at one of assets / sites / zones / devices /
        # categories depending on the parent label's entity_type, so
        # no concrete FK is possible. Orphan cleanup happens in the
        # entity-delete handlers (Phase B).
        col = EntityLabelModel.__table__.columns["entity_id"]
        assert isinstance(col.type, UUID)
        assert col.foreign_keys == set()
        assert col.index is True  # needed for "list labels on entity"

    def test_value_max_length(self) -> None:
        col = EntityLabelModel.__table__.columns["value"]
        assert isinstance(col.type, String)
        assert col.type.length == 64
        assert col.nullable is False


class TestPhaseAModelCanRoundtrip:
    """Instantiating the ORM objects does not blow up.

    Doesn't touch a database — just confirms the mapped_column defaults
    line up so Phase B's repository can construct rows without surprise.
    """

    def test_label_construct_with_defaults(self) -> None:
        label = LabelModel(
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            key="location",
        )
        assert label.entity_type == "asset"
        assert label.key == "location"
        assert label.color is None
        # id default is uuid.uuid4 — applied at flush, not on construct.

    def test_entity_label_construct(self) -> None:
        assoc = EntityLabelModel(
            label_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            value="warehouse-a",
        )
        assert assoc.value == "warehouse-a"
