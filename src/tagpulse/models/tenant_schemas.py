"""Tenant schemas for API requests and responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    """Create a new tenant."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    plan: str = Field(default="standard", max_length=50)


class TenantResponse(BaseModel):
    """Tenant returned from the API."""

    id: UUID
    name: str
    slug: str
    plan: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UsageRecord(BaseModel):
    """A single usage record for a tenant."""

    tenant_id: UUID
    usage_date: datetime
    dimension: str
    quantity: int
    unit: str


class UsageSummary(BaseModel):
    """Aggregated usage summary for a billing period."""

    tenant_id: UUID
    dimension: str
    total_quantity: int
    unit: str
