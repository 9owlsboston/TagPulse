"""API-route tests for Sprint 41 Phase B2 cap enforcement + ?kind filter.

Exercises ``POST /rules``, ``PATCH /rules/{id}``, and ``GET /rules`` for
the new Phase B behaviors:

* ``?kind=signaling|legacy`` query parameter on list
* ``HTTP 409`` translation of :class:`SignalingScopeCapExceededError`
* ``?override=true`` admin-only bypass with audit-log entry
* Editor cannot use ``?override=true`` (HTTP 403)

Uses the existing in-process FastAPI app + dependency_overrides pattern
established by ``test_sprint28_telemetry_model_patch.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tagpulse.api.routes import rules as rules_routes
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user
from tagpulse.models.rule_schemas import RuleResponse
from tagpulse.repositories.timescaledb.session import get_session
from tagpulse.rules import SignalingScopeCapExceededError


def _make_response(
    tenant_id: UUID,
    *,
    event_type: str | None = None,
    name: str = "r",
) -> RuleResponse:
    now = datetime.now(UTC)
    return RuleResponse(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=None,
        condition_type=(f"signaling.{event_type}.periodic" if event_type else "threshold"),
        condition_config=({"cadence_minutes": 5} if event_type else {}),
        action_type="notification",
        action_config={},
        scope_device_id=None,
        enabled=True,
        created_at=now,
        updated_at=now,
        event_type=event_type,
        trigger="periodic" if event_type else None,
    )


class _FakeRulesService:
    """In-process stand-in for ``RulesService`` covering the surfaces
    the cap-enforcement routes exercise. Each instance is one-shot
    (one HTTP call per test) so test isolation is preserved."""

    def __init__(
        self,
        *,
        list_result: list[RuleResponse] | None = None,
        create_result: RuleResponse | None = None,
        update_result: RuleResponse | None = None,
        cap_violation: SignalingScopeCapExceededError | None = None,
    ) -> None:
        self.list_result = list_result or []
        self.create_result = create_result
        self.update_result = update_result
        self.cap_violation = cap_violation
        self.list_kwargs: dict[str, Any] = {}
        self.create_kwargs: dict[str, Any] = {}
        self.update_kwargs: dict[str, Any] = {}

    async def list_rules(
        self,
        tenant_id: UUID,
        *,
        enabled_only: bool = False,
        kind: str | None = None,
    ) -> list[RuleResponse]:
        self.list_kwargs = {
            "tenant_id": tenant_id,
            "enabled_only": enabled_only,
            "kind": kind,
        }
        return self.list_result

    async def create_rule(
        self,
        tenant_id: UUID,
        body: Any,
        *,
        allow_cap_override: bool = False,
    ) -> RuleResponse:
        self.create_kwargs = {
            "tenant_id": tenant_id,
            "body": body,
            "allow_cap_override": allow_cap_override,
        }
        if self.cap_violation and not allow_cap_override:
            raise self.cap_violation
        assert self.create_result is not None
        return self.create_result

    async def update_rule(
        self,
        tenant_id: UUID,
        rule_id: UUID,
        patch: Any,
        *,
        allow_cap_override: bool = False,
    ) -> RuleResponse | None:
        self.update_kwargs = {
            "tenant_id": tenant_id,
            "rule_id": rule_id,
            "patch": patch,
            "allow_cap_override": allow_cap_override,
        }
        if self.cap_violation and not allow_cap_override:
            raise self.cap_violation
        return self.update_result


def _make_app(
    fake: _FakeRulesService,
    *,
    role: str = "admin",
    audit_calls: list[dict[str, Any]] | None = None,
) -> tuple[FastAPI, UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    app = FastAPI()
    app.include_router(rules_routes.router)

    def _user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_name="T",
            tenant_slug="t",
            role=role,
        )

    async def _session():  # type: ignore[no-untyped-def]
        # The route handler passes the session to RulesService(...) but
        # our monkeypatched class ignores it. The session also flows
        # into AuditLogger(); we replace AuditLogger below so this
        # mock is fine.
        class _Sentinel:
            pass

        yield _Sentinel()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    return app, tenant_id, user_id


# ---------------------------------------------------------------------------
# GET /rules?kind=...
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rules_passes_kind_signaling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRulesService(list_result=[])
    app, _, _ = _make_app(fake, role="viewer")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/rules?kind=signaling")
    assert r.status_code == 200
    assert fake.list_kwargs["kind"] == "signaling"


@pytest.mark.asyncio
async def test_list_rules_passes_kind_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRulesService(list_result=[])
    app, _, _ = _make_app(fake, role="viewer")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/rules?kind=legacy")
    assert r.status_code == 200
    assert fake.list_kwargs["kind"] == "legacy"


@pytest.mark.asyncio
async def test_list_rules_kind_omitted_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default GET /rules call must still send ``kind=None`` so the
    service returns all rules \u2014 backwards-compatible behavior."""
    fake = _FakeRulesService(list_result=[])
    app, _, _ = _make_app(fake, role="viewer")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/rules")
    assert r.status_code == 200
    assert fake.list_kwargs["kind"] is None


@pytest.mark.asyncio
async def test_list_rules_invalid_kind_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown kind value must be rejected by FastAPI's Literal
    validation as HTTP 422."""
    fake = _FakeRulesService(list_result=[])
    app, _, _ = _make_app(fake, role="viewer")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/rules?kind=bogus")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /rules with cap enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_rules_returns_409_on_cap_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cap violation surfaces as HTTP 409 with a structured detail
    body containing the scope, current count, and cap."""

    cat = uuid4()
    tenant = uuid4()
    cap_err = SignalingScopeCapExceededError(
        tenant_id=tenant,
        event_type="location",
        category_id=cat,
        current_count=5,
        cap=5,
    )
    fake = _FakeRulesService(cap_violation=cap_err)
    app, _, _ = _make_app(fake, role="editor")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    payload = {
        "name": "r",
        "condition_type": "signaling.location.periodic",
        "condition_config": {"cadence_minutes": 5},
        "action_type": "notification",
        "action_config": {},
        "category_ids": [str(cat)],
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/rules", json=payload)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["error"] == "signaling_scope_cap_exceeded"
    assert body["detail"]["event_type"] == "location"
    assert body["detail"]["current_count"] == 5
    assert body["detail"]["cap"] == 5
    assert body["detail"]["category_id"] == str(cat)


@pytest.mark.asyncio
async def test_post_rules_override_requires_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editor role passing ?override=true gets HTTP 403, not 409."""

    cap_err = SignalingScopeCapExceededError(
        tenant_id=uuid4(),
        event_type="location",
        category_id=uuid4(),
        current_count=5,
        cap=5,
    )
    fake = _FakeRulesService(cap_violation=cap_err)
    app, _, _ = _make_app(fake, role="editor")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    payload = {
        "name": "r",
        "condition_type": "signaling.location.periodic",
        "condition_config": {"cadence_minutes": 5},
        "action_type": "notification",
        "action_config": {},
        "category_ids": [str(uuid4())],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/rules?override=true", json=payload)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_rules_admin_override_bypasses_cap_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin + ?override=true: 201, cap_violation bypassed, audit-log
    entry written."""

    cat = uuid4()
    tenant = uuid4()
    cap_err = SignalingScopeCapExceededError(
        tenant_id=tenant,
        event_type="location",
        category_id=cat,
        current_count=5,
        cap=5,
    )
    created = _make_response(tenant, event_type="location", name="bypassed")
    fake = _FakeRulesService(cap_violation=cap_err, create_result=created)
    app, _, _ = _make_app(fake, role="admin")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    audit_calls: list[dict[str, Any]] = []

    class _FakeAudit:
        def __init__(self, *, session: Any) -> None:
            pass

        async def log(
            self,
            *,
            tenant_id: UUID,
            user_id: UUID | None,
            action: str,
            resource_type: str,
            resource_id: UUID,
            changes: dict[str, Any] | None = None,
        ) -> None:
            audit_calls.append(
                {
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "changes": changes,
                }
            )

    monkeypatch.setattr(rules_routes, "AuditLogger", _FakeAudit)

    payload = {
        "name": "bypassed",
        "condition_type": "signaling.location.periodic",
        "condition_config": {"cadence_minutes": 5},
        "action_type": "notification",
        "action_config": {},
        "category_ids": [str(cat)],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/rules?override=true", json=payload)
    assert r.status_code == 201
    assert fake.create_kwargs["allow_cap_override"] is True
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "signaling.cap_override"
    assert audit_calls[0]["resource_type"] == "rule"
    assert str(cat) in audit_calls[0]["changes"]["category_ids"]


@pytest.mark.asyncio
async def test_post_rules_override_on_legacy_rule_no_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy rule (no event_type) created with ?override=true is
    pass-through \u2014 no audit-log entry because the cap doesn't apply."""

    tenant = uuid4()
    created = _make_response(tenant, event_type=None)
    fake = _FakeRulesService(create_result=created)
    app, _, _ = _make_app(fake, role="admin")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    audit_calls: list[dict[str, Any]] = []

    class _FakeAudit:
        def __init__(self, *, session: Any) -> None:
            pass

        async def log(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(rules_routes, "AuditLogger", _FakeAudit)

    payload = {
        "name": "legacy",
        "condition_type": "threshold",
        "condition_config": {},
        "action_type": "notification",
        "action_config": {},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/rules?override=true", json=payload)
    assert r.status_code == 201
    assert audit_calls == []


# ---------------------------------------------------------------------------
# PATCH /rules/{id} with cap enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rules_returns_409_on_cap_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap_err = SignalingScopeCapExceededError(
        tenant_id=uuid4(),
        event_type="temperature",
        category_id=uuid4(),
        current_count=5,
        cap=5,
    )
    fake = _FakeRulesService(cap_violation=cap_err)
    app, _, _ = _make_app(fake, role="editor")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/rules/{uuid4()}", json={"enabled": True})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "signaling_scope_cap_exceeded"


@pytest.mark.asyncio
async def test_patch_rules_admin_override_bypasses_cap_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cat = uuid4()
    tenant = uuid4()
    cap_err = SignalingScopeCapExceededError(
        tenant_id=tenant,
        event_type="location",
        category_id=cat,
        current_count=5,
        cap=5,
    )
    updated = _make_response(tenant, event_type="location", name="bumped")
    fake = _FakeRulesService(cap_violation=cap_err, update_result=updated)
    app, _, _ = _make_app(fake, role="admin")
    monkeypatch.setattr(rules_routes, "RulesService", lambda session: fake)

    audit_calls: list[dict[str, Any]] = []

    class _FakeAudit:
        def __init__(self, *, session: Any) -> None:
            pass

        async def log(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(rules_routes, "AuditLogger", _FakeAudit)

    rule_id = uuid4()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/rules/{rule_id}?override=true", json={"enabled": True})
    assert r.status_code == 200
    assert fake.update_kwargs["allow_cap_override"] is True
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "signaling.cap_override"
