"""Sprint 28 G1 — PATCH /telemetry-models/{model_id} unit tests.

Covers:
* PATCH updates the metrics list and emits a ``telemetry_model.updated`` audit log.
* PATCH against an unknown id returns 404.
* PATCH against another tenant's id returns 404 (cross-tenant isolation).
* The schema rejects an empty metrics list.
* PATCH ignores any extra fields the client sends — only ``metrics`` is mutable.

Uses the in-process FastAPI app + an in-memory fake service so the tests
run without a database, mirroring the pattern used by
``test_sprint19_subject_telemetry.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.dependencies import get_telemetry_model_service
from tagpulse.api.routes import telemetry_models as tm_routes
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.schemas import (
    MetricDefinition,
    TelemetryModelResponse,
    TelemetryModelUpdate,
)


class _FakeService:
    """In-memory stand-in for ``TelemetryModelService`` for the PATCH path.

    Stores models keyed on ``(tenant_id, model_id)`` and records every audit
    call so tests can assert the emitted action/resource fields.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[UUID, UUID], TelemetryModelResponse] = {}
        self.audit_calls: list[dict[str, object]] = []

    def seed(self, tenant_id: UUID, model: TelemetryModelResponse) -> None:
        self.store[(tenant_id, model.id)] = model

    async def update(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        model_id: UUID,
        patch: TelemetryModelUpdate,
    ) -> TelemetryModelResponse | None:
        existing = self.store.get((tenant_id, model_id))
        if existing is None:
            return None
        new_metrics = list(patch.metrics)
        updated = existing.model_copy(
            update={"metrics": new_metrics, "updated_at": datetime.now(UTC)}
        )
        self.store[(tenant_id, model_id)] = updated
        self.audit_calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "action": "telemetry_model.updated",
                "resource_type": "telemetry_model",
                "resource_id": model_id,
                "metrics_count": len(new_metrics),
            }
        )
        return updated


def _make_app(fake: _FakeService, *, role: str = "editor") -> tuple[FastAPI, UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    app = FastAPI()
    app.include_router(tm_routes.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    async def _service():
        yield fake

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_telemetry_model_service] = _service
    return app, tenant_id, user_id


def _seed_model(tenant_id: UUID) -> TelemetryModelResponse:
    return TelemetryModelResponse(
        id=uuid4(),
        subject_kind="device",
        device_type="rfid-reader",
        metrics=[MetricDefinition(name="temperature", unit="C", min_value=-40, max_value=85)],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_patch_updates_metrics_and_audits() -> None:
    fake = _FakeService()
    app, tenant_id, user_id = _make_app(fake)
    model = _seed_model(tenant_id)
    fake.seed(tenant_id, model)

    new_metrics = [
        {"name": "temperature", "unit": "C", "min_value": -40, "max_value": 85},
        {"name": "humidity", "unit": "%", "min_value": 0, "max_value": 100},
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/telemetry-models/{model.id}", json={"metrics": new_metrics})

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(model.id)
    assert len(body["metrics"]) == 2
    assert body["device_type"] == "rfid-reader"  # immutable, preserved

    assert len(fake.audit_calls) == 1
    audit = fake.audit_calls[0]
    assert audit["action"] == "telemetry_model.updated"
    assert audit["resource_type"] == "telemetry_model"
    assert audit["resource_id"] == model.id
    assert audit["tenant_id"] == tenant_id
    assert audit["user_id"] == user_id
    assert audit["metrics_count"] == 2


@pytest.mark.asyncio
async def test_patch_unknown_id_returns_404() -> None:
    fake = _FakeService()
    app, _, _ = _make_app(fake)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/telemetry-models/{uuid4()}",
            json={"metrics": [{"name": "x", "unit": "u"}]},
        )
    assert r.status_code == 404
    assert fake.audit_calls == []


@pytest.mark.asyncio
async def test_patch_other_tenants_id_returns_404() -> None:
    """Cross-tenant isolation: a model owned by tenant A is invisible to tenant B."""
    fake = _FakeService()
    other_tenant = uuid4()
    other_model = _seed_model(other_tenant)
    fake.seed(other_tenant, other_model)

    app, _, _ = _make_app(fake)  # caller's tenant is fresh, not other_tenant
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/telemetry-models/{other_model.id}",
            json={"metrics": [{"name": "x", "unit": "u"}]},
        )
    assert r.status_code == 404
    assert fake.audit_calls == []


@pytest.mark.asyncio
async def test_patch_rejects_empty_metrics_list() -> None:
    fake = _FakeService()
    app, tenant_id, _ = _make_app(fake)
    model = _seed_model(tenant_id)
    fake.seed(tenant_id, model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/telemetry-models/{model.id}", json={"metrics": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_ignores_immutable_fields_in_body() -> None:
    """Only ``metrics`` is read; ``subject_kind`` and ``device_type`` in the
    payload are silently dropped by the schema (extra='ignore' default)."""
    fake = _FakeService()
    app, tenant_id, _ = _make_app(fake)
    model = _seed_model(tenant_id)
    fake.seed(tenant_id, model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/telemetry-models/{model.id}",
            json={
                "metrics": [{"name": "x", "unit": "u"}],
                "subject_kind": "asset",
                "device_type": "different-type",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["subject_kind"] == "device"
    assert body["device_type"] == "rfid-reader"


@pytest.mark.asyncio
async def test_viewer_role_cannot_patch() -> None:
    fake = _FakeService()
    app, tenant_id, _ = _make_app(fake, role="viewer")
    model = _seed_model(tenant_id)
    fake.seed(tenant_id, model)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/telemetry-models/{model.id}",
            json={"metrics": [{"name": "x", "unit": "u"}]},
        )
    assert r.status_code == 403
    assert fake.audit_calls == []


# Silence "imported but unused" for the uuid module if linting tightens later.
_ = uuid
