"""Unit tests for user auth and API key management."""

import pytest

from tagpulse.core.user_auth import generate_api_key, verify_api_key
from tagpulse.models.user_schemas import UserCreate, UserUpdate


class TestApiKeyGeneration:
    def test_generate_key_format(self) -> None:
        raw, prefix, key_hash = generate_api_key("acme-corp")
        assert raw.startswith("tp_acme-corp_")
        assert len(prefix) == 10
        assert len(key_hash) == 64  # SHA-256 hex

    def test_verify_correct_key(self) -> None:
        raw, _, key_hash = generate_api_key("test")
        assert verify_api_key(raw, key_hash) is True

    def test_verify_wrong_key(self) -> None:
        _, _, key_hash = generate_api_key("test")
        assert verify_api_key("tp_test_wrong", key_hash) is False

    def test_keys_are_unique(self) -> None:
        raw1, _, _ = generate_api_key("test")
        raw2, _, _ = generate_api_key("test")
        assert raw1 != raw2


class TestUserSchemas:
    def test_valid_user_create(self) -> None:
        user = UserCreate(email="ops@example.com", name="Ops User")
        assert user.role == "viewer"

    def test_valid_admin(self) -> None:
        user = UserCreate(email="admin@example.com", name="Admin", role="admin")
        assert user.role == "admin"

    def test_invalid_role(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            UserCreate(email="x@y.com", name="X", role="superuser")

    def test_update_partial(self) -> None:
        patch = UserUpdate(role="editor")
        assert patch.model_dump(exclude_unset=True) == {"role": "editor"}
