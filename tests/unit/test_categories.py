"""Unit tests for Sprint 34 Categories (ADR 019) — schemas & route helpers."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from tagpulse.api.routes.categories import _diff
from tagpulse.models.schemas import (
    AssetCreate,
    AssetUpdate,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)
from tagpulse.models.user_schemas import UserCreate, UserUpdate


class TestCategoryCreate:
    """Validators on POST /categories payload."""

    def test_minimum_shape_accepted(self) -> None:
        body = CategoryCreate(name="Pallet", category_type="object")
        assert body.name == "Pallet"
        assert body.category_type == "object"
        assert body.required_tags == 1
        assert body.sku_upc is None
        assert body.description is None

    @pytest.mark.parametrize(
        "category_type",
        ["liquid_container", "reference_tag", "rti_container", "object"],
    )
    def test_all_four_category_types_accepted(self, category_type: str) -> None:
        body = CategoryCreate(name="X", category_type=category_type)  # type: ignore[arg-type]
        assert body.category_type == category_type

    def test_unknown_category_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="X", category_type="widget")  # type: ignore[arg-type]

    def test_required_tags_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="X", category_type="object", required_tags=0)
        with pytest.raises(ValidationError):
            CategoryCreate(name="X", category_type="object", required_tags=-3)

    def test_required_tags_greater_than_one_accepted(self) -> None:
        body = CategoryCreate(name="Pallet", category_type="object", required_tags=4)
        assert body.required_tags == 4

    def test_name_required(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="", category_type="object")

    def test_name_max_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="x" * 256, category_type="object")

    def test_sku_upc_max_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="X", category_type="object", sku_upc="0" * 65)

    def test_category_type_is_required(self) -> None:
        with pytest.raises(ValidationError):
            CategoryCreate(name="X")  # type: ignore[call-arg]


class TestCategoryUpdate:
    """Validators on PATCH /categories/{id} payload."""

    def test_empty_payload_accepted(self) -> None:
        payload = CategoryUpdate()
        assert payload.model_dump(exclude_unset=True) == {}

    def test_partial_update_keeps_other_fields_unset(self) -> None:
        payload = CategoryUpdate(name="Renamed")
        provided = payload.model_dump(exclude_unset=True)
        assert provided == {"name": "Renamed"}

    def test_category_type_is_not_a_declared_field(self) -> None:
        # ADR 019 makes category_type immutable. Pydantic must drop
        # the smuggled field; the router separately rejects it with
        # 400.
        assert "category_type" not in CategoryUpdate.model_fields

    def test_required_tags_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CategoryUpdate(required_tags=0)


class TestCategoryResponse:
    """from_attributes flag lets us hydrate from the ORM row."""

    def test_from_attributes_enabled(self) -> None:
        assert CategoryResponse.model_config.get("from_attributes") is True


class TestAssetSchemasCategoryId:
    """Sprint 34: AssetCreate / AssetUpdate gain category_id."""

    def test_category_id_optional_on_create(self) -> None:
        body = AssetCreate(name="Drum-42", asset_type="drum")
        assert body.category_id is None

    def test_category_id_round_trip(self) -> None:
        from uuid import uuid4

        cid = uuid4()
        body = AssetCreate(name="Drum-42", asset_type="drum", category_id=cid)
        assert body.category_id == cid

    def test_category_id_optional_on_update(self) -> None:
        patch = AssetUpdate(name="Renamed")
        assert "category_id" not in patch.model_dump(exclude_unset=True)


class TestAssetExternalRefValidator:
    """Gap 2.8: external_ref must reject URL-unsafe characters."""

    def test_empty_string_normalises_to_none(self) -> None:
        body = AssetCreate(name="X", asset_type="drum", external_ref="")
        assert body.external_ref is None

    def test_whitespace_only_normalises_to_none(self) -> None:
        body = AssetCreate(name="X", asset_type="drum", external_ref="   ")
        assert body.external_ref is None

    def test_safe_value_accepted(self) -> None:
        body = AssetCreate(name="X", asset_type="drum", external_ref="ACME-DRUM-42")
        assert body.external_ref == "ACME-DRUM-42"

    def test_safe_value_with_underscore_and_hyphen_accepted(self) -> None:
        body = AssetCreate(name="X", asset_type="drum", external_ref="acme_drum-42_v2")
        assert body.external_ref == "acme_drum-42_v2"

    @pytest.mark.parametrize(
        "bad_char",
        [
            ".",
            ":",
            "/",
            "?",
            "#",
            "\\",
            "[",
            "]",
            "@",
            ",",
            "|",
            "&",
            "!",
            "=",
            "$",
            "'",
            "*",
            "+",
            ";",
            "%",
        ],
    )
    def test_each_forbidden_char_rejected_on_create(self, bad_char: str) -> None:
        with pytest.raises(ValidationError):
            AssetCreate(name="X", asset_type="drum", external_ref=f"acme{bad_char}drum")

    @pytest.mark.parametrize(
        "bad_char",
        [".", ":", "/", "?", "#", "@", "%"],
    )
    def test_forbidden_chars_also_rejected_on_update(self, bad_char: str) -> None:
        with pytest.raises(ValidationError):
            AssetUpdate(external_ref=f"acme{bad_char}drum")


class TestInstallerRole:
    """Gap 2.6: ``installer`` joins admin/editor/viewer."""

    @pytest.mark.parametrize("role", ["admin", "editor", "viewer", "installer"])
    def test_role_accepted_on_user_create(self, role: str) -> None:
        body = UserCreate(email="a@example.com", name="Alice", role=role)
        assert body.role == role

    @pytest.mark.parametrize("role", ["admin", "editor", "viewer", "installer"])
    def test_role_accepted_on_user_update(self, role: str) -> None:
        patch = UserUpdate(role=role)
        assert patch.role == role

    @pytest.mark.parametrize("role", ["owner", "root", "guest", "INSTALLER"])
    def test_unknown_or_wrong_case_roles_rejected(self, role: str) -> None:
        with pytest.raises(ValidationError):
            UserCreate(email="a@example.com", name="Alice", role=role)


class TestRouterDiffHelper:
    """`_diff` powers the PATCH audit log entries."""

    @staticmethod
    def _sample(**overrides: object) -> CategoryResponse:
        from datetime import datetime
        from uuid import uuid4

        now = datetime.now(UTC)
        defaults: dict[str, object] = {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "name": "Pallet",
            "sku_upc": None,
            "description": None,
            "category_type": "object",
            "required_tags": 1,
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return CategoryResponse(**defaults)  # type: ignore[arg-type]

    def test_no_change_returns_empty_dict(self) -> None:
        row = self._sample()
        assert _diff(row, row) == {}

    def test_single_field_change_recorded(self) -> None:
        before = self._sample(name="Pallet")
        after = self._sample(
            id=before.id,
            tenant_id=before.tenant_id,
            name="Pallet-EUR",
            created_at=before.created_at,
            updated_at=before.updated_at,
        )
        assert _diff(before, after) == {"name": {"from": "Pallet", "to": "Pallet-EUR"}}

    def test_category_type_is_never_in_diff(self) -> None:
        # Even if a (forbidden) category_type change somehow slipped
        # through, the audit diff would not include it because the
        # diff fields list omits it.
        before = self._sample(category_type="object")
        after = self._sample(
            id=before.id,
            tenant_id=before.tenant_id,
            category_type="rti_container",
            created_at=before.created_at,
            updated_at=before.updated_at,
        )
        diff = _diff(before, after)
        assert "category_type" not in diff
