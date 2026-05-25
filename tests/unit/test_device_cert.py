"""Sprint 17b — device cert attachment (thumbprint computation + RBAC)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest


@pytest.fixture
def self_signed_pem() -> tuple[str, str]:
    """Generate a fresh self-signed cert and return (pem, expected_thumbprint)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-device.tagpulse.io")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=90))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    der = cert.public_bytes(serialization.Encoding.DER)
    thumbprint = hashlib.sha256(der).hexdigest()
    return pem, thumbprint


class _FakeDevice:
    def __init__(self, device_id: UUID, tenant_id: UUID) -> None:
        self.id = device_id
        self.tenant_id = tenant_id
        self.cert_thumbprint: str | None = None
        self.cert_subject: str | None = None


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(
        self,
        tenant_id: UUID,
        *,
        action: str,
        resource_type: str,
        resource_id: UUID,
        changes: dict[str, Any],
        user_id: UUID | None = None,
    ) -> None:
        self.entries.append(
            {
                "tenant_id": tenant_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "changes": changes,
            }
        )


class _FakeSession:
    def __init__(self, device: _FakeDevice) -> None:
        self._device = device
        self.flushed = False

    async def execute(self, _stmt: Any) -> Any:
        device = self._device

        class _Result:
            def scalar_one_or_none(self) -> Any:  # noqa: ARG002
                return device

        return _Result()

    async def flush(self) -> None:
        self.flushed = True


class _FakeUser:
    def __init__(self, tenant_id: UUID, user_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id


@pytest.mark.asyncio
async def test_attach_cert_computes_thumbprint(
    monkeypatch: pytest.MonkeyPatch,
    self_signed_pem: tuple[str, str],
) -> None:
    from tagpulse.api.routes.devices import (
        DeviceCertAttach,
        attach_device_cert,
    )

    pem, expected = self_signed_pem
    tenant = uuid4()
    user = uuid4()
    device_id = uuid4()
    fake_device = _FakeDevice(device_id, tenant)
    fake_session = _FakeSession(fake_device)
    fake_audit = _FakeAudit()

    monkeypatch.setattr("tagpulse.api.routes.devices.AuditLogger", lambda _s: fake_audit)

    response = await attach_device_cert(
        device_id=device_id,
        body=DeviceCertAttach(cert_pem=pem),
        user=_FakeUser(tenant, user),  # type: ignore[arg-type]
        session=fake_session,  # type: ignore[arg-type]
    )

    assert response.thumbprint == expected
    assert response.subject is not None
    assert "test-device" in response.subject
    assert fake_device.cert_thumbprint == expected
    assert fake_session.flushed is True
    assert any(e["action"] == "device.cert_attached" for e in fake_audit.entries)


@pytest.mark.asyncio
async def test_attach_cert_rejects_invalid_pem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from tagpulse.api.routes.devices import (
        DeviceCertAttach,
        attach_device_cert,
    )

    tenant = uuid4()
    fake_device = _FakeDevice(uuid4(), tenant)
    fake_session = _FakeSession(fake_device)
    fake_audit = _FakeAudit()
    monkeypatch.setattr("tagpulse.api.routes.devices.AuditLogger", lambda _s: fake_audit)

    with pytest.raises(HTTPException) as exc_info:
        await attach_device_cert(
            device_id=fake_device.id,
            body=DeviceCertAttach(cert_pem="not a real pem"),
            user=_FakeUser(tenant, uuid4()),  # type: ignore[arg-type]
            session=fake_session,  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 422
