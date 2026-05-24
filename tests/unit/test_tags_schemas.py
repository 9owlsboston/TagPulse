"""Schema-level validation tests for Sprint 50 tag registry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import TagCreate, TagTransferRequest


class TestTagCreate:
    def test_accepts_canonical_epc(self) -> None:
        tag = TagCreate(epc_hex="3034257BF400B7800004CB2F")
        assert tag.source == "api"
        assert tag.metadata is None

    def test_rejects_lowercase(self) -> None:
        # The schema regex is strict — normalisation happens in the
        # route. A direct construction with lowercase is a 422.
        with pytest.raises(ValidationError):
            TagCreate(epc_hex="3034257bf400b7800004cb2f")

    def test_rejects_short(self) -> None:
        with pytest.raises(ValidationError):
            TagCreate(epc_hex="ABCDEF01")  # 8 chars < 16 min

    def test_rejects_non_hex(self) -> None:
        with pytest.raises(ValidationError):
            TagCreate(epc_hex="ZZZZ" * 8)

    def test_source_transfer_in_rejected(self) -> None:
        # transfer_in is server-only; the create endpoint excludes it.
        with pytest.raises(ValidationError):
            TagCreate(epc_hex="A" * 16, source="transfer_in")  # type: ignore[arg-type]


class TestTagTransferRequest:
    def test_minimum_one_epc(self) -> None:
        with pytest.raises(ValidationError):
            TagTransferRequest(to_tenant_slug="other", epcs=[])

    def test_caps_at_1000(self) -> None:
        epcs = ["A" * 16] * 1001
        with pytest.raises(ValidationError):
            TagTransferRequest(to_tenant_slug="other", epcs=epcs)

    def test_accepts_basic(self) -> None:
        req = TagTransferRequest(to_tenant_slug="acme", epcs=["A" * 16])
        assert req.to_tenant_slug == "acme"
        assert len(req.epcs) == 1
