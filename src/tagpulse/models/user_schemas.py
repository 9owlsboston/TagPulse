"""Pydantic schemas for users and API key management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """Create a new user within a tenant."""

    email: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(default="viewer", pattern=r"^(admin|editor|viewer|installer)$")


class UserUpdate(BaseModel):
    """Partial update for a user."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, pattern=r"^(admin|editor|viewer|installer)$")
    status: str | None = Field(default=None, pattern=r"^(active|inactive)$")


class UserResponse(BaseModel):
    """User returned from the API."""

    id: UUID
    tenant_id: UUID
    email: str
    name: str
    role: str
    status: str
    api_key_prefix: str | None
    api_key_created_at: datetime | None = None
    created_at: datetime
    last_login: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyResponse(BaseModel):
    """Returned once when an API key is generated."""

    api_key: str
    prefix: str
    message: str = "Store this key securely — it cannot be retrieved again."
