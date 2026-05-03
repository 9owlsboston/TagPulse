"""Unit tests for tag_data 4 KB cap (Sprint 14)."""

import json
from uuid import uuid4

from tagpulse.ingestion.tag_data import TAG_DATA_MAX_BYTES, cap_tag_data


class TestCapTagData:
    def test_under_cap_unchanged(self) -> None:
        payload = {"temperature_c": 4.2, "battery_pct": 87}
        result = cap_tag_data(payload, tenant_id=str(uuid4()))
        assert result == payload
        assert "_truncated" not in result

    def test_empty_unchanged(self) -> None:
        result = cap_tag_data({}, tenant_id=str(uuid4()))
        assert result == {}

    def test_over_cap_truncated_with_marker(self) -> None:
        # Build a payload well over 4 KB (compact JSON encoding is what
        # cap_tag_data measures against).
        payload: dict[str, object] = {f"k{i}": "x" * 100 for i in range(60)}
        encoded_size = len(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        assert encoded_size > TAG_DATA_MAX_BYTES
        result = cap_tag_data(payload, tenant_id=str(uuid4()))
        assert result.get("_truncated") is True
        # Final payload (compact) must fit within the cap.
        capped_size = len(
            json.dumps(result, separators=(",", ":")).encode("utf-8")
        )
        assert capped_size <= TAG_DATA_MAX_BYTES
        # Some keys should have been dropped.
        assert len(result) < len(payload) + 1
