"""Unit tests for tenant schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.tenant_schemas import TenantCreate


class TestTenantCreate:
    def test_valid(self) -> None:
        t = TenantCreate(name="Acme Corp", slug="acme-corp")
        assert t.plan == "standard"

    def test_valid_with_plan(self) -> None:
        t = TenantCreate(name="BigCo", slug="bigco", plan="enterprise")
        assert t.plan == "enterprise"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreate(name="", slug="valid")

    def test_empty_slug_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreate(name="Valid", slug="")

    def test_slug_uppercase_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreate(name="Valid", slug="INVALID")

    def test_slug_spaces_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantCreate(name="Valid", slug="has spaces")

    def test_slug_valid_chars(self) -> None:
        t = TenantCreate(name="Test", slug="my-tenant-123")
        assert t.slug == "my-tenant-123"
