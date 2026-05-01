"""Unit tests for authentication — JWT, login schemas, rate limiting."""

import time

import jwt
import pytest

from tagpulse.api.routes.auth import (
    LoginRequest,
    LoginResponse,
    LoginUserInfo,
    _check_rate_limit,
    _login_attempts,
)
from tagpulse.core.config import settings
from tagpulse.core.user_auth import (
    AuthenticatedUser,
    create_jwt,
    decode_jwt,
    generate_api_key,
    verify_api_key,
)


class TestLoginSchemas:
    """Validate login request/response schemas."""

    def test_valid_login_request(self) -> None:
        req = LoginRequest(email="admin@example.com", api_key="tp_test_abc123")
        assert req.email == "admin@example.com"
        assert req.api_key == "tp_test_abc123"

    def test_empty_email_rejected(self) -> None:
        with pytest.raises(ValueError):  # noqa: PT011 — pydantic raises ValidationError (subclass)
            LoginRequest(email="", api_key="tp_test_abc123")

    def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError):  # noqa: PT011
            LoginRequest(email="admin@example.com", api_key="")

    def test_login_response_structure(self) -> None:
        resp = LoginResponse(
            access_token="token123",  # noqa: S106 — test fixture, not a real credential
            expires_in=3600,
            user=LoginUserInfo(
                id="user-uuid",
                email="admin@example.com",
                name="Admin",
                role="admin",
                tenant_id="tenant-uuid",
                tenant_name="Test Corp",
            ),
        )
        assert resp.token_type == "bearer"  # noqa: S105 — string literal compare, not a credential
        assert resp.expires_in == 3600
        assert resp.user.role == "admin"


class TestJWT:
    """Test JWT creation and decoding."""

    def test_create_and_decode_jwt(self) -> None:
        """Round-trip: create a JWT and decode it back."""

        class FakeUser:
            id = "e91e853d-d27c-4242-b88f-824aadc97d2f"
            email = "admin@example.com"
            role = "admin"

        class FakeTenant:
            id = "11111111-1111-1111-1111-111111111111"
            name = "Test Corp"
            slug = "test-corp"

        token = create_jwt(FakeUser(), FakeTenant())  # type: ignore[arg-type]
        payload = decode_jwt(token)

        assert payload["sub"] == "e91e853d-d27c-4242-b88f-824aadc97d2f"
        assert payload["tid"] == "11111111-1111-1111-1111-111111111111"
        assert payload["role"] == "admin"
        assert payload["email"] == "admin@example.com"
        assert payload["tenant_name"] == "Test Corp"
        assert payload["tenant_slug"] == "test-corp"
        assert payload["iss"] == "tagpulse"

    def test_expired_jwt_raises(self) -> None:
        """Expired tokens should raise 401."""
        payload = {
            "sub": "user-id",
            "tid": "tenant-id",
            "role": "admin",
            "iss": "tagpulse",
            "exp": int(time.time()) - 10,
        }
        token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_jwt(token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_invalid_jwt_raises(self) -> None:
        """Tokens signed with wrong key should raise 401."""
        payload = {
            "sub": "user-id",
            "tid": "tenant-id",
            "role": "admin",
            "iss": "tagpulse",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_jwt(token)
        assert exc_info.value.status_code == 401

    def test_wrong_issuer_raises(self) -> None:
        """Tokens with wrong issuer should raise 401."""
        payload = {
            "sub": "user-id",
            "tid": "tenant-id",
            "role": "admin",
            "iss": "not-tagpulse",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_jwt(token)
        assert exc_info.value.status_code == 401


class TestRateLimiting:
    """Test login rate limiting."""

    def setup_method(self) -> None:
        _login_attempts.clear()

    def test_allows_under_limit(self) -> None:
        for _ in range(settings.login_rate_limit):
            _check_rate_limit("10.0.0.1")
        # Should not raise for exactly limit attempts

    def test_blocks_over_limit(self) -> None:
        from fastapi import HTTPException

        for _ in range(settings.login_rate_limit):
            _check_rate_limit("10.0.0.2")
        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit("10.0.0.2")
        assert exc_info.value.status_code == 429

    def test_different_ips_independent(self) -> None:
        for _ in range(settings.login_rate_limit):
            _check_rate_limit("10.0.0.3")
        # Different IP should still work
        _check_rate_limit("10.0.0.4")

    def test_window_expires(self) -> None:
        """Old attempts should be pruned after the window."""
        _login_attempts["10.0.0.5"] = [time.monotonic() - 120.0] * 10
        # All old — should be pruned and allow new attempt
        _check_rate_limit("10.0.0.5")


class TestAuthenticatedUser:
    """Test AuthenticatedUser from JWT payload."""

    def test_jwt_auth_creates_user(self) -> None:
        """Verify AuthenticatedUser can be built from JWT payload fields."""

        class FakeUser:
            id = "e91e853d-d27c-4242-b88f-824aadc97d2f"
            email = "editor@example.com"
            role = "editor"

        class FakeTenant:
            id = "11111111-1111-1111-1111-111111111111"
            name = "Test Corp"
            slug = "test-corp"

        token = create_jwt(FakeUser(), FakeTenant())  # type: ignore[arg-type]
        payload = decode_jwt(token)

        user = AuthenticatedUser(
            user_id=payload["sub"],
            tenant_id=payload["tid"],
            tenant_name=payload["tenant_name"],
            tenant_slug=payload["tenant_slug"],
            role=payload["role"],
            email=payload.get("email"),
        )
        assert user.role == "editor"
        assert user.email == "editor@example.com"


class TestAPIKeyVerification:
    """Test API key generation and verification."""

    def test_generated_key_verifies(self) -> None:
        raw_key, prefix, key_hash = generate_api_key("test-corp")
        assert raw_key.startswith("tp_test-corp_")
        assert len(prefix) == 10
        assert verify_api_key(raw_key, key_hash)

    def test_wrong_key_fails(self) -> None:
        _, _, key_hash = generate_api_key("test-corp")
        assert not verify_api_key("tp_test-corp_wrongkey", key_hash)
