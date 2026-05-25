"""Unit tests for integration schemas."""

import pytest
from pydantic import ValidationError

from tagpulse.models.integration_schemas import IntegrationCreate, IntegrationUpdate


class TestIntegrationCreate:
    def test_valid_webhook(self) -> None:
        i = IntegrationCreate(
            name="My Webhook",
            type="webhook",
            events=["tag_read.created"],
            config={"url": "https://example.com/hook"},
        )
        assert i.type == "webhook"
        assert i.enabled is True

    def test_valid_sse(self) -> None:
        i = IntegrationCreate(
            name="Live Feed",
            type="sse",
            events=["tag_read.created", "alert.triggered"],
            config={},
        )
        assert i.type == "sse"

    def test_valid_export(self) -> None:
        i = IntegrationCreate(
            name="Daily CSV",
            type="export",
            events=["tag_read.created"],
            config={"format": "csv", "schedule": "0 6 * * *"},
        )
        assert i.type == "export"

    def test_invalid_type(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationCreate(name="Bad", type="ftp", events=["x"], config={})

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationCreate(name="", type="webhook", events=["x"], config={})

    def test_empty_events_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IntegrationCreate(name="X", type="webhook", events=[], config={})


class TestIntegrationUpdate:
    def test_all_optional(self) -> None:
        patch = IntegrationUpdate()
        assert patch.model_dump(exclude_unset=True) == {}

    def test_partial(self) -> None:
        patch = IntegrationUpdate(name="Renamed", enabled=False)
        dumped = patch.model_dump(exclude_unset=True)
        assert dumped == {"name": "Renamed", "enabled": False}
