"""Application configuration via environment variables."""

from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """TagPulse application settings."""

    database_url: str = "postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse"
    database_config_path: str = "config/database.yaml"
    database_backend: Literal["timescale", "postgres"] = "timescale"
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"
    event_bus_capacity: int = 10_000
    cors_origins: str = "http://localhost:5173"
    jwt_secret: str = "dev-secret-change-in-production"  # noqa: S105 — dev default, overridden in production via env
    jwt_expiry_seconds: int = 3600
    login_rate_limit: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
