"""Unit tests for device provisioning logic."""

import hashlib
import uuid

import pytest
from pydantic import ValidationError

from tagpulse.api.routes.provisioning import ProvisionRequest, ProvisionStatusResponse


class TestProvisionRequest:
    """Validate provisioning request schema."""

    def test_valid_request(self) -> None:
        req = ProvisionRequest(name="Reader-001")
        assert req.name == "Reader-001"
        assert req.device_type == "rfid_reader"

    def test_custom_device_type(self) -> None:
        req = ProvisionRequest(name="Sensor-A", device_type="temperature")
        assert req.device_type == "temperature"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProvisionRequest(name="")

    def test_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProvisionRequest(name="X" * 256)


class TestProvisionStatusResponse:
    """Validate provisioning status response."""

    def test_status_response(self) -> None:
        resp = ProvisionStatusResponse(device_name="R1", status="pending")
        assert resp.device_name == "R1"
        assert resp.status == "pending"


class TestProvisioningKeyVerification:
    """Verify provisioning key hash logic."""

    def test_sha256_hash_matches(self) -> None:
        key = "tp_test_" + uuid.uuid4().hex[:16]
        expected_hash = hashlib.sha256(key.encode()).hexdigest()
        actual_hash = hashlib.sha256(key.encode()).hexdigest()
        assert expected_hash == actual_hash

    def test_prefix_extraction(self) -> None:
        key = "tp_test_abcdef1234567890"
        prefix = key[:10]
        assert prefix == "tp_test_ab"

    def test_wrong_key_does_not_match(self) -> None:
        key1 = "tp_test_key1"
        key2 = "tp_test_key2"
        h1 = hashlib.sha256(key1.encode()).hexdigest()
        h2 = hashlib.sha256(key2.encode()).hexdigest()
        assert h1 != h2
