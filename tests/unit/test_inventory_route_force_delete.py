"""Sprint 59 (§59.6): ``DELETE /stock-items/{id}?force=true`` of a moved unit
returns a structured 409, never a 500.

Pins the route → service contract using the ``TestClient`` + stub-service
pattern (no DB): when the service raises ``StockItemLedgerError`` the route
must translate it into a 409 whose JSON ``detail`` is a structured object the
UI can branch on (``error``/``movement_count``/``remediation``).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.dependencies import get_inventory_service
from tagpulse.api.routes.inventory import router
from tagpulse.api.services.inventory_service import StockItemLedgerError
from tagpulse.core.user_auth import AuthenticatedUser, get_current_user


class _LedgerStubService:
    """Stub whose ``delete_stock_item`` always reports a ledger conflict."""

    def __init__(self) -> None:
        self.movement_count = 3

    async def delete_stock_item(
        self, tenant_id: UUID, user_id: UUID | None, stock_item_id: UUID, *, force: bool = False
    ) -> bool:
        raise StockItemLedgerError(stock_item_id, self.movement_count)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")

    def _admin_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=uuid4(),
            tenant_id=uuid4(),
            tenant_name="t",
            tenant_slug="t",
            role="admin",
        )

    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_inventory_service] = lambda: _LedgerStubService()
    return TestClient(app)


def test_force_delete_moved_unit_returns_structured_409(client: TestClient) -> None:
    response = client.delete(f"/v1/stock-items/{uuid4()}", params={"force": "true"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["error"] == "stock_item_has_ledger"
    assert detail["movement_count"] == 3
    assert "state=consumed" in detail["remediation"]
