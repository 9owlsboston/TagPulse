"""Unit tests for device provisioning logic."""

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tagpulse.api.routes.provisioning import (
    ProvisionRequest,
    ProvisionResponse,
    ProvisionStatusResponse,
    provision_device,
)


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


class _FakeResult:
    def __init__(self, scalar: object) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar


class _FakeSession:
    """Minimal async session: returns a fixed tenant, records adds/flush."""

    def __init__(self, tenant: object) -> None:
        self._tenant = tenant
        self.added: list = []
        self.flushed = False

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._tenant)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


class TestProvisionMintsToken:
    """Sprint 78 (I-K6D1): provision issues the device token once, up front."""

    @pytest.mark.asyncio
    async def test_provision_returns_pending_device_token(self) -> None:
        key = "pk_acme_0123456789abcdef"
        tenant = SimpleNamespace(
            id=uuid.uuid4(),
            slug="acme",
            status="active",
            provisioning_key_hash=hashlib.sha256(key.encode()).hexdigest(),
        )
        session = _FakeSession(tenant)

        resp = await provision_device(
            ProvisionRequest(name="Reader-42"),
            key=key,
            session=session,  # type: ignore[arg-type]
        )

        assert isinstance(resp, ProvisionResponse)
        assert resp.status == "pending"
        assert resp.token.startswith("tpd_acme_")
        assert resp.token_prefix == resp.token[:10]
        # Device row was created with the hashed token (never the plaintext).
        assert session.flushed is True
        (device,) = session.added
        assert device.status == "pending"
        assert device.token_hash == hashlib.sha256(resp.token.encode()).hexdigest()
        assert device.token_hash != resp.token
