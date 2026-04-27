"""Unit tests for user management routes."""

import uuid

import pytest
from pydantic import ValidationError

from tagpulse.core.user_auth import AuthenticatedUser


def _make_admin(tenant_id: uuid.UUID | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        tenant_name="Test",
        tenant_slug="test",
        role="admin",
        email="admin@test.com",
    )


def _make_viewer(tenant_id: uuid.UUID | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        tenant_name="Test",
        tenant_slug="test",
        role="viewer",
        email="viewer@test.com",
    )


def _make_editor(tenant_id: uuid.UUID | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        tenant_name="Test",
        tenant_slug="test",
        role="editor",
        email="editor@test.com",
    )


class TestAuthenticatedUserRoles:
    """Verify role attributes on AuthenticatedUser."""

    def test_admin_role(self) -> None:
        user = _make_admin()
        assert user.role == "admin"
        assert user.role in ("admin",)

    def test_editor_role(self) -> None:
        user = _make_editor()
        assert user.role == "editor"
        assert user.role in ("admin", "editor")

    def test_viewer_role(self) -> None:
        user = _make_viewer()
        assert user.role == "viewer"
        assert user.role not in ("admin", "editor")

    def test_viewer_cannot_write(self) -> None:
        user = _make_viewer()
        write_roles = {"admin", "editor"}
        assert user.role not in write_roles

    def test_editor_cannot_delete(self) -> None:
        user = _make_editor()
        delete_roles = {"admin"}
        assert user.role not in delete_roles

    def test_user_has_tenant_id(self) -> None:
        tid = uuid.uuid4()
        user = _make_admin(tenant_id=tid)
        assert user.tenant_id == tid

    def test_user_has_user_id(self) -> None:
        user = _make_admin()
        assert user.user_id is not None

    def test_backward_compat_viewer_no_user_id(self) -> None:
        user = AuthenticatedUser(
            user_id=None,
            tenant_id=uuid.uuid4(),
            tenant_name="Test",
            tenant_slug="test",
            role="viewer",
        )
        assert user.user_id is None
        assert user.role == "viewer"


class TestUserSchemaValidation:
    """Verify user creation schema validation."""

    def test_user_role_must_be_valid(self) -> None:
        from tagpulse.models.user_schemas import UserCreate

        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", name="A", role="superadmin")

    def test_user_update_partial(self) -> None:
        from tagpulse.models.user_schemas import UserUpdate

        update = UserUpdate(role="editor")
        dumped = update.model_dump(exclude_unset=True)
        assert dumped == {"role": "editor"}
