"""Unit tests for the health check endpoint."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes.health import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
