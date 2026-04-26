"""Unit tests for tag read Pydantic schemas."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import TagReadCreate, TagReadResponse


class TestTagReadCreate:
    def test_valid_minimal(self) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="ABCD1234",
            timestamp=datetime.now(UTC),
        )
        assert read.signal_strength is None
        assert read.sensor_data is None

    def test_valid_full(self) -> None:
        read = TagReadCreate(
            device_id=uuid4(),
            tag_id="ABCD1234",
            timestamp=datetime.now(UTC),
            signal_strength=-45.2,
            sensor_data={"temperature": 23.5},
        )
        assert read.signal_strength == -45.2
        assert read.sensor_data == {"temperature": 23.5}

    def test_empty_tag_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TagReadCreate(
                device_id=uuid4(),
                tag_id="",
                timestamp=datetime.now(UTC),
            )

    def test_missing_device_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TagReadCreate(
                tag_id="ABCD1234",
                timestamp=datetime.now(UTC),
            )  # type: ignore[call-arg]

    def test_missing_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TagReadCreate(
                device_id=uuid4(),
                tag_id="ABCD1234",
            )  # type: ignore[call-arg]


class TestTagReadResponse:
    def test_from_attributes(self) -> None:
        now = datetime.now(UTC)
        rid = uuid4()
        did = uuid4()

        class FakeRow:
            id = rid
            device_id = did
            tag_id = "TAG001"
            timestamp = now
            signal_strength = -50.0
            sensor_data = None
            created_at = now

        resp = TagReadResponse.model_validate(FakeRow(), from_attributes=True)
        assert resp.id == rid
        assert resp.device_id == did
        assert resp.tag_id == "TAG001"
