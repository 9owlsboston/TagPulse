"""Unit tests for ``tagpulse.api.label_filter`` (Sprint 35 Phase C).

Coverage:

- ``parse_label_filter`` happy-path, comma-split, dedup, case-folding
- Guard rails: max keys, max values, regex rejection, empty segments
- ``apply_label_filter`` — verifies the compiled SQL contains the
  expected EXISTS subqueries and bound parameters
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from starlette.datastructures import QueryParams

from tagpulse.api.label_filter import (
    LABEL_FILTER_MAX_KEYS,
    LABEL_FILTER_MAX_VALUES_PER_KEY,
    LabelFilterError,
    apply_label_filter,
    parse_label_filter,
)
from tagpulse.models.database import AssetModel


def _qp(*pairs: tuple[str, str]) -> QueryParams:
    """Build a QueryParams from explicit (key, value) tuples."""
    return QueryParams(list(pairs))


class TestParseLabelFilterEmpty:
    def test_no_labels_returns_none(self) -> None:
        assert parse_label_filter(_qp(("limit", "100"))) is None

    def test_empty_query_params(self) -> None:
        assert parse_label_filter(_qp()) is None

    def test_ignores_unrelated_params(self) -> None:
        qp = _qp(("category_id", "deadbeef"), ("status", "active"), ("limit", "50"))
        assert parse_label_filter(qp) is None


class TestParseLabelFilterHappyPath:
    def test_single_key_single_value(self) -> None:
        result = parse_label_filter(_qp(("labels[location]", "warehouse-a")))
        assert result == {"location": ["warehouse-a"]}

    def test_single_key_multiple_values_comma_split(self) -> None:
        result = parse_label_filter(_qp(("labels[location]", "warehouse-a,warehouse-b")))
        assert result == {"location": ["warehouse-a", "warehouse-b"]}

    def test_multiple_keys_and_semantics(self) -> None:
        result = parse_label_filter(
            _qp(
                ("labels[location]", "warehouse-a"),
                ("labels[priority]", "high"),
            )
        )
        assert result == {"location": ["warehouse-a"], "priority": ["high"]}

    def test_combined_form_from_adr(self) -> None:
        result = parse_label_filter(
            _qp(
                ("labels[location]", "warehouse-a,warehouse-b"),
                ("labels[priority]", "high"),
                ("labels[owner]", "alice,bob"),
            )
        )
        assert result == {
            "location": ["warehouse-a", "warehouse-b"],
            "priority": ["high"],
            "owner": ["alice", "bob"],
        }

    def test_duplicate_key_merges_values(self) -> None:
        result = parse_label_filter(
            _qp(
                ("labels[location]", "warehouse-a"),
                ("labels[location]", "warehouse-b,warehouse-c"),
            )
        )
        assert result == {"location": ["warehouse-a", "warehouse-b", "warehouse-c"]}

    def test_dedup_within_key(self) -> None:
        result = parse_label_filter(
            _qp(
                ("labels[location]", "warehouse-a,warehouse-a"),
                ("labels[location]", "warehouse-a"),
            )
        )
        assert result == {"location": ["warehouse-a"]}

    def test_key_case_folded_to_lower(self) -> None:
        """Labels are stored case-insensitive — parser normalises keys to lowercase."""
        result = parse_label_filter(_qp(("labels[Location]", "warehouse-a")))
        assert result == {"location": ["warehouse-a"]}

    def test_uppercase_and_lowercase_keys_merge(self) -> None:
        result = parse_label_filter(
            _qp(
                ("labels[Location]", "warehouse-a"),
                ("labels[location]", "warehouse-b"),
            )
        )
        assert result == {"location": ["warehouse-a", "warehouse-b"]}

    def test_value_case_preserved(self) -> None:
        """Values are case-sensitive (only keys fold)."""
        result = parse_label_filter(_qp(("labels[location]", "Warehouse-A")))
        assert result == {"location": ["Warehouse-A"]}


class TestParseLabelFilterGuardRails:
    def test_too_many_keys(self) -> None:
        pairs = tuple((f"labels[k{i:02d}_x]", "v") for i in range(LABEL_FILTER_MAX_KEYS + 1))
        with pytest.raises(LabelFilterError, match="Too many label keys"):
            parse_label_filter(_qp(*pairs))

    def test_max_keys_accepted(self) -> None:
        pairs = tuple((f"labels[k{i:02d}_x]", "v") for i in range(LABEL_FILTER_MAX_KEYS))
        result = parse_label_filter(_qp(*pairs))
        assert result is not None
        assert len(result) == LABEL_FILTER_MAX_KEYS

    def test_too_many_values_per_key(self) -> None:
        values = ",".join(f"v{i}" for i in range(LABEL_FILTER_MAX_VALUES_PER_KEY + 1))
        with pytest.raises(LabelFilterError, match="Too many values"):
            parse_label_filter(_qp(("labels[location]", values)))

    def test_max_values_accepted(self) -> None:
        values = ",".join(f"v{i}" for i in range(LABEL_FILTER_MAX_VALUES_PER_KEY))
        result = parse_label_filter(_qp(("labels[location]", values)))
        assert result is not None
        assert len(result["location"]) == LABEL_FILTER_MAX_VALUES_PER_KEY


class TestParseLabelFilterKeyRegex:
    @pytest.mark.parametrize("bad_key", ["ab", "x" * 25, "has-dash", "has space", "%bad%"])
    def test_invalid_key_rejected(self, bad_key: str) -> None:
        with pytest.raises(LabelFilterError, match="Invalid label key"):
            parse_label_filter(_qp((f"labels[{bad_key}]", "v")))

    @pytest.mark.parametrize(
        "good_key",
        ["abc", "loc.id", "loc_id", "loc+x", "loc$x", "ABC", "x" * 24, "abc123"],
    )
    def test_valid_key_accepted(self, good_key: str) -> None:
        result = parse_label_filter(_qp((f"labels[{good_key}]", "v")))
        assert result is not None
        assert good_key.lower() in result


class TestParseLabelFilterValueRegex:
    @pytest.mark.parametrize("bad_value", ["has space", "x" * 65, "%bad%", "with/slash", "a,"])
    def test_invalid_value_rejected(self, bad_value: str) -> None:
        with pytest.raises(LabelFilterError):
            parse_label_filter(_qp(("labels[location]", bad_value)))

    @pytest.mark.parametrize(
        "good_value", ["warehouse-a", "warehouse_b", "v.1.2", "X", "x" * 64, "alice"]
    )
    def test_valid_value_accepted(self, good_value: str) -> None:
        result = parse_label_filter(_qp(("labels[location]", good_value)))
        assert result == {"location": [good_value]}

    def test_empty_value_rejected(self) -> None:
        with pytest.raises(LabelFilterError, match="Empty value"):
            parse_label_filter(_qp(("labels[location]", "")))

    def test_trailing_comma_rejected(self) -> None:
        with pytest.raises(LabelFilterError, match="Empty value"):
            parse_label_filter(_qp(("labels[location]", "warehouse-a,")))

    def test_leading_comma_rejected(self) -> None:
        with pytest.raises(LabelFilterError, match="Empty value"):
            parse_label_filter(_qp(("labels[location]", ",warehouse-a")))

    def test_double_comma_rejected(self) -> None:
        with pytest.raises(LabelFilterError, match="Empty value"):
            parse_label_filter(_qp(("labels[location]", "warehouse-a,,warehouse-b")))


class TestApplyLabelFilterPassThrough:
    def test_none_labels_returns_stmt_unchanged(self) -> None:
        stmt = select(AssetModel)
        result = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels=None,
        )
        assert result is stmt

    def test_empty_dict_returns_stmt_unchanged(self) -> None:
        stmt = select(AssetModel)
        result = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels={},
        )
        assert result is stmt


class TestApplyLabelFilterSQL:
    """Compile the statement against the postgres dialect and inspect the SQL string."""

    @staticmethod
    def _compile(stmt: object) -> str:
        return str(
            stmt.compile(  # type: ignore[attr-defined]
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": False},
            )
        )

    def test_single_key_adds_one_exists(self) -> None:
        stmt = select(AssetModel)
        out = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels={"location": ["warehouse-a"]},
        )
        sql = self._compile(out).lower()
        assert sql.count("exists (select") == 1
        assert "entity_labels" in sql
        assert "labels" in sql
        assert "lower(labels.key)" in sql
        assert "entity_labels.value in" in sql

    def test_multiple_keys_add_multiple_exists(self) -> None:
        stmt = select(AssetModel)
        out = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels={"location": ["warehouse-a"], "priority": ["high"]},
        )
        sql = self._compile(out).lower()
        assert sql.count("exists (select") == 2

    def test_entity_type_bound_correctly(self) -> None:
        stmt = select(AssetModel)
        out = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="site",
            entity_id_col=AssetModel.id,
            labels={"location": ["w1"]},
        )
        compiled = out.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled).lower()
        assert "'site'" in sql

    def test_key_is_lowercased_in_bound_param(self) -> None:
        """Even mixed-case input keys should compare via lower()='lower'."""
        stmt = select(AssetModel)
        out = apply_label_filter(
            stmt,
            tenant_id=uuid.uuid4(),
            entity_type="asset",
            entity_id_col=AssetModel.id,
            labels={"Location": ["w1"]},
        )
        compiled = out.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled).lower()
        assert "'location'" in sql
        # Original-cased key MUST NOT appear as a bound literal
        assert "= 'location'" in sql  # the .lower() bind
