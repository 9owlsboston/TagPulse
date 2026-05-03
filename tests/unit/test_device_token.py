"""Sprint 16 — device token generator tests (ADR-011 Phase 1)."""

import hashlib

from tagpulse.core.user_auth import generate_device_token, verify_api_key


class TestGenerateDeviceToken:
    def test_token_format(self) -> None:
        raw, prefix, token_hash = generate_device_token("acme")
        assert raw.startswith("tpd_acme_")
        assert prefix == raw[:10]
        assert len(token_hash) == 64  # sha256 hex

    def test_hash_matches(self) -> None:
        raw, _, token_hash = generate_device_token("acme")
        assert hashlib.sha256(raw.encode()).hexdigest() == token_hash

    def test_uniqueness(self) -> None:
        tokens = {generate_device_token("acme")[0] for _ in range(50)}
        assert len(tokens) == 50

    def test_distinguishable_from_user_keys(self) -> None:
        # User keys use ``tp_`` prefix, device tokens ``tpd_``.
        raw, _, _ = generate_device_token("acme")
        assert raw.startswith("tpd_") and not raw.startswith("tp_a")

    def test_verify_helper_works_with_device_token(self) -> None:
        raw, _, token_hash = generate_device_token("acme")
        assert verify_api_key(raw, token_hash) is True
        assert verify_api_key(raw + "x", token_hash) is False
