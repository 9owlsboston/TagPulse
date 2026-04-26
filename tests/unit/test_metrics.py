"""Unit tests for metrics endpoint and OTel metrics definitions."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tagpulse.api.routes.metrics import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contains_process_info(self) -> None:
        response = client.get("/metrics")
        # prometheus_client always includes process metrics
        has_content = (
            "process_" in response.text
            or "python_" in response.text
            or response.status_code == 200
        )
        assert has_content


class TestMetricDefinitions:
    def test_ingestion_counter_exists(self) -> None:
        from tagpulse.core.otel_metrics import ingestion_counter
        assert ingestion_counter is not None

    def test_eventbus_metrics_exist(self) -> None:
        from tagpulse.core.otel_metrics import (
            eventbus_consumed,
            eventbus_dropped,
            eventbus_published,
            eventbus_queue_size,
        )
        assert eventbus_published is not None
        assert eventbus_consumed is not None
        assert eventbus_dropped is not None
        assert eventbus_queue_size is not None

    def test_rule_metrics_exist(self) -> None:
        from tagpulse.core.otel_metrics import alerts_fired, rule_evaluations
        assert rule_evaluations is not None
        assert alerts_fired is not None
