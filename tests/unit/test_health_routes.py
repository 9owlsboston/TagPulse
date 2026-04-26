"""Unit tests for health check routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes.health import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestLiveness:
    def test_health_returns_ok(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
