"""Unit tests for telemetry model schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.schemas import MetricDefinition, TelemetryModelCreate


class TestMetricDefinition:
    def test_valid(self) -> None:
        m = MetricDefinition(name="signal_strength", unit="dBm", min_value=-100, max_value=0)
        assert m.name == "signal_strength"
        assert m.min_value == -100

    def test_no_range(self) -> None:
        m = MetricDefinition(name="temperature", unit="°C")
        assert m.min_value is None
        assert m.max_value is None

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricDefinition(name="", unit="dBm")


class TestTelemetryModelCreate:
    def test_valid(self) -> None:
        model = TelemetryModelCreate(
            device_type="rfid_reader",
            metrics=[MetricDefinition(name="signal_strength", unit="dBm")],
        )
        assert model.device_type == "rfid_reader"
        assert len(model.metrics) == 1

    def test_empty_metrics_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryModelCreate(device_type="rfid_reader", metrics=[])

    def test_empty_device_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryModelCreate(
                device_type="",
                metrics=[MetricDefinition(name="x", unit="y")],
            )
