"""Unit tests for device Pydantic schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import DeviceCreate, DeviceStatusUpdate, DeviceUpdate


class TestDeviceCreate:
    def test_valid_minimal(self) -> None:
        device = DeviceCreate(name="Reader-01")
        assert device.device_type == "rfid_reader"
        assert device.metadata is None
        assert device.configuration is None
        assert device.firmware_version is None

    def test_valid_full(self) -> None:
        device = DeviceCreate(
            name="Reader-01",
            device_type="rfid_reader",
            metadata={"location": "warehouse-a"},
            configuration={"power_level": 30},
            firmware_version="1.2.0",
        )
        assert device.metadata == {"location": "warehouse-a"}
        assert device.configuration == {"power_level": 30}
        assert device.firmware_version == "1.2.0"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCreate(name="")

    def test_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCreate(name="x" * 256)


class TestDeviceUpdate:
    def test_all_fields_optional(self) -> None:
        patch = DeviceUpdate()
        assert patch.model_dump(exclude_unset=True) == {}

    def test_partial_update(self) -> None:
        patch = DeviceUpdate(name="New Name")
        dumped = patch.model_dump(exclude_unset=True)
        assert dumped == {"name": "New Name"}

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeviceUpdate(name="")


class TestDeviceStatusUpdate:
    def test_valid(self) -> None:
        status = DeviceStatusUpdate(connection_state="online", firmware_version="2.0.1")
        assert status.connection_state == "online"
        assert status.firmware_version == "2.0.1"

    def test_valid_without_firmware(self) -> None:
        status = DeviceStatusUpdate(connection_state="offline")
        assert status.firmware_version is None

    def test_connection_state_too_long(self) -> None:
        with pytest.raises(ValidationError):
            DeviceStatusUpdate(connection_state="x" * 51)
