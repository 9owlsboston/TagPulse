"""Unit tests for Sprint 35 Labels (ADR 020) — schemas & route helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tagpulse.api.routes.labels import _diff
from tagpulse.models.schemas import (
    LabelAssociationCreate,
    LabelAssociationResponse,
    LabelCreate,
    LabelResponse,
    LabelUpdate,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _make_response(
    *,
    key: str = "location",
    color: str | None = "#3366ff",
    entity_type: str = "asset",
    label_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> LabelResponse:
    return LabelResponse(
        id=label_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        entity_type=entity_type,  # type: ignore[arg-type]
        key=key,
        color=color,
        created_by=None,
        updated_by=None,
        created_at=_utc_now(),
        updated_at=_utc_now(),
    )


class TestLabelCreate:
    """Validators on POST /labels payload."""

    def test_minimum_shape_accepted(self) -> None:
        body = LabelCreate(entity_type="asset", key="loc")
        assert body.entity_type == "asset"
        assert body.key == "loc"
        assert body.color is None

    @pytest.mark.parametrize(
        "entity_type",
        ["asset", "site", "zone", "device", "category"],
    )
    def test_all_five_entity_types_accepted(self, entity_type: str) -> None:
        body = LabelCreate(entity_type=entity_type, key="key1")  # type: ignore[arg-type]
        assert body.entity_type == entity_type

    def test_unknown_entity_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LabelCreate(entity_type="widget", key="loc")  # type: ignore[arg-type]

    def test_key_too_short_rejected(self) -> None:
        # min_length=3 per migration 039 regex
        with pytest.raises(ValidationError):
            LabelCreate(entity_type="asset", key="ab")

    def test_key_too_long_rejected(self) -> None:
        # max_length=24 per migration 039 regex
        with pytest.raises(ValidationError):
            LabelCreate(entity_type="asset", key="x" * 25)

    @pytest.mark.parametrize(
        "key",
        ["loc-a", "loc/a", "loc a", "loc!", "loc@", "loc#", "loc%", "loc&"],
    )
    def test_key_with_disallowed_chars_rejected(self, key: str) -> None:
        # Pattern: ^[A-Za-z0-9_.+$]{3,24}$
        with pytest.raises(ValidationError):
            LabelCreate(entity_type="asset", key=key)

    @pytest.mark.parametrize(
        "key",
        ["loc", "loc_a", "loc.a", "loc+a", "loc$a", "Loc123", "ABC.def_42"],
    )
    def test_key_with_allowed_chars_accepted(self, key: str) -> None:
        body = LabelCreate(entity_type="asset", key=key)
        assert body.key == key

    @pytest.mark.parametrize(
        "color",
        ["#000000", "#ffffff", "#3366ff", "#A1B2C3"],
    )
    def test_color_hex_accepted(self, color: str) -> None:
        body = LabelCreate(entity_type="asset", key="loc", color=color)
        assert body.color == color

    @pytest.mark.parametrize(
        "color",
        ["#fff", "#1234567", "red", "rgb(0,0,0)", "3366ff", "#zzzzzz"],
    )
    def test_color_non_hex_rejected(self, color: str) -> None:
        with pytest.raises(ValidationError):
            LabelCreate(entity_type="asset", key="loc", color=color)

    def test_color_none_accepted(self) -> None:
        body = LabelCreate(entity_type="asset", key="loc", color=None)
        assert body.color is None


class TestLabelUpdate:
    """Validators on PATCH /labels/{id} payload."""

    def test_empty_payload_accepted(self) -> None:
        payload = LabelUpdate()
        assert payload.model_dump(exclude_unset=True) == {}

    def test_partial_update_keeps_other_fields_unset(self) -> None:
        payload = LabelUpdate(key="renamed")
        provided = payload.model_dump(exclude_unset=True)
        assert provided == {"key": "renamed"}

    def test_entity_type_is_not_a_declared_field(self) -> None:
        # ADR 020 makes entity_type immutable. Pydantic must drop
        # the smuggled field; the router separately rejects it with
        # 400 against the raw model_dump.
        assert "entity_type" not in LabelUpdate.model_fields

    def test_key_pattern_enforced_on_update(self) -> None:
        with pytest.raises(ValidationError):
            LabelUpdate(key="x")

    def test_color_pattern_enforced_on_update(self) -> None:
        with pytest.raises(ValidationError):
            LabelUpdate(color="not-a-hex")

    def test_explicit_color_none_clears(self) -> None:
        # Setting color=None explicitly is a deliberate "clear" and
        # must appear in exclude_unset=True payload.
        payload = LabelUpdate(color=None)
        provided = payload.model_dump(exclude_unset=True)
        assert provided == {"color": None}


class TestLabelAssociationCreate:
    """Validators on POST /{entity_type}/{id}/labels payload."""

    def test_minimum_shape_accepted(self) -> None:
        body = LabelAssociationCreate(key="location", value="warehouse-a")
        assert body.key == "location"
        assert body.value == "warehouse-a"

    def test_value_too_long_rejected(self) -> None:
        # max_length=64 per migration 039 regex
        with pytest.raises(ValidationError):
            LabelAssociationCreate(key="location", value="x" * 65)

    def test_value_empty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LabelAssociationCreate(key="location", value="")

    @pytest.mark.parametrize(
        "value",
        ["warehouse-a", "wh.1", "wh_1", "WH-42", "1.2.3", "a", "x" * 64],
    )
    def test_value_with_allowed_chars_accepted(self, value: str) -> None:
        body = LabelAssociationCreate(key="location", value=value)
        assert body.value == value

    @pytest.mark.parametrize(
        "value",
        ["wh a", "wh/a", "wh!", "wh@", "wh+", "wh$", "wh:1"],
    )
    def test_value_with_disallowed_chars_rejected(self, value: str) -> None:
        # Pattern: ^[A-Za-z0-9._-]{1,64}$ — hyphen IS allowed (ADR
        # 020 contradiction-fix in PR #37), but space/colon/plus etc
        # are not.
        with pytest.raises(ValidationError):
            LabelAssociationCreate(key="location", value=value)


class TestLabelAssociationResponse:
    """Smoke test the joined response shape."""

    def test_construct_from_kwargs(self) -> None:
        now = _utc_now()
        label_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        body = LabelAssociationResponse(
            label_id=label_id,
            entity_id=entity_id,
            entity_type="asset",
            key="location",
            value="warehouse-a",
            color="#3366ff",
            created_by=None,
            created_at=now,
        )
        assert body.label_id == label_id
        assert body.entity_id == entity_id
        assert body.color == "#3366ff"


class TestLabelDiffHelper:
    """The route layer's :func:`_diff` builds the audit ``changes`` dict.

    Sites and Categories use the same shape — see ``_diff`` in
    ``categories.py``. This mirrors that contract for Labels.
    """

    def test_no_changes_returns_empty_dict(self) -> None:
        before = _make_response(key="location", color="#3366ff")
        after = LabelResponse(**{**before.model_dump(), "updated_at": _utc_now()})
        # Only updated_at changed — _diff intentionally excludes
        # that field.
        assert _diff(before, after) == {}

    def test_key_change_recorded(self) -> None:
        before = _make_response(key="location", color="#3366ff")
        after = LabelResponse(**{**before.model_dump(), "key": "loc", "updated_at": _utc_now()})
        changes = _diff(before, after)
        assert changes == {"key": {"from": "location", "to": "loc"}}

    def test_color_change_recorded(self) -> None:
        before = _make_response(key="location", color="#3366ff")
        after = LabelResponse(
            **{**before.model_dump(), "color": "#ff0000", "updated_at": _utc_now()}
        )
        changes = _diff(before, after)
        assert changes == {"color": {"from": "#3366ff", "to": "#ff0000"}}

    def test_color_set_to_none_recorded(self) -> None:
        before = _make_response(key="location", color="#3366ff")
        after = LabelResponse(**{**before.model_dump(), "color": None, "updated_at": _utc_now()})
        changes = _diff(before, after)
        assert changes == {"color": {"from": "#3366ff", "to": None}}

    def test_both_changes_recorded(self) -> None:
        before = _make_response(key="location", color="#3366ff")
        after = LabelResponse(
            **{
                **before.model_dump(),
                "key": "loc",
                "color": None,
                "updated_at": _utc_now(),
            }
        )
        changes = _diff(before, after)
        assert changes == {
            "key": {"from": "location", "to": "loc"},
            "color": {"from": "#3366ff", "to": None},
        }
