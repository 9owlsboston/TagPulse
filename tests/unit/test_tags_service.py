"""Unit tests for ``tagpulse.services.tags`` (Sprint 50, ADR 028)."""

from __future__ import annotations

import pytest

from tagpulse.services.tags import (
    StatusTransitionError,
    normalize_epc_hex,
    parse_gs1_uri,
    validate_status_transition,
)


class TestNormalizeEpcHex:
    def test_uppercases(self) -> None:
        assert normalize_epc_hex("abcdef01") == "ABCDEF01"

    def test_strips_whitespace(self) -> None:
        assert normalize_epc_hex("  3034257bf400b7800004cb2f  ") == "3034257BF400B7800004CB2F"

    def test_idempotent_on_canonical(self) -> None:
        canon = "3034257BF400B7800004CB2F"
        assert normalize_epc_hex(canon) == canon


class TestParseGs1Uri:
    """parse_gs1_uri is intentionally lenient — never raises."""

    def test_known_sgtin_returns_uri(self) -> None:
        # SGTIN-96 header (0x30) with GS1 company prefix encoded
        uri = parse_gs1_uri("3034257BF400B7800004CB2F")
        assert uri is not None
        assert uri.startswith("urn:epc:id:sgtin:")

    def test_raw_header_returns_none(self) -> None:
        # 0xFF is not a known EPC header.
        assert parse_gs1_uri("FF" + "00" * 11) is None

    def test_malformed_hex_returns_none(self) -> None:
        # Short / odd-length / non-hex inputs must not raise.
        assert parse_gs1_uri("") is None
        assert parse_gs1_uri("ZZZZ") is None
        assert parse_gs1_uri("123") is None


class TestValidateStatusTransition:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("registered", "retired"),
            ("registered", "defective"),
            ("active", "retired"),
            ("active", "defective"),
            # Same-state PATCH is allowed (idempotent).
            ("registered", "registered"),
            ("retired", "retired"),
            ("transferred_out", "transferred_out"),
        ],
    )
    def test_allowed(self, current: str, target: str) -> None:
        validate_status_transition(current, target)  # no raise

    @pytest.mark.parametrize(
        "current,target",
        [
            # registrar-worker domain (not via operator API)
            ("registered", "active"),
            # transfer-flow domain
            ("active", "transferred_out"),
            ("registered", "transferred_out"),
            # un-retiring is forbidden
            ("retired", "active"),
            ("retired", "registered"),
            ("defective", "active"),
            # cannot resurrect a transferred-out tag
            ("transferred_out", "active"),
            ("transferred_out", "registered"),
        ],
    )
    def test_rejected(self, current: str, target: str) -> None:
        with pytest.raises(StatusTransitionError):
            validate_status_transition(current, target)
