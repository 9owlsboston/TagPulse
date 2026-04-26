"""Pydantic schemas for integrations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IntegrationCreate(BaseModel):
    """Create an integration target."""

    name: str = Field(min_length=1, max_length=255)
    type: str = Field(pattern=r"^(webhook|sse|export)$")
    events: list[str] = Field(min_length=1)
    config: dict[str, Any]
    filters: list[dict[str, Any]] | None = None
    enrichments: dict[str, str] | None = None
    enabled: bool = True


class IntegrationUpdate(BaseModel):
    """Partial update for an integration."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    events: list[str] | None = None
    config: dict[str, Any] | None = None
    filters: list[dict[str, Any]] | None = None
    enrichments: dict[str, str] | None = None
    enabled: bool | None = None


class IntegrationResponse(BaseModel):
    """Integration returned from the API."""

    id: UUID
    tenant_id: UUID
    name: str
    type: str
    events: list[str]
    config: dict[str, Any]
    enabled: bool
    status: str
    health_status: str
    filters: list[dict[str, Any]] | None
    enrichments: dict[str, str] | None
    last_triggered: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeliveryResponse(BaseModel):
    """Integration delivery log entry."""

    id: UUID
    integration_id: UUID
    event_type: str
    status: str
    attempts: int
    response_code: int | None
    error_message: str | None
    created_at: datetime
