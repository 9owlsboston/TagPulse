"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """TagPulse application settings."""

    database_url: str = "postgresql+asyncpg://tagpulse:secret@localhost:5432/tagpulse"
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"
    event_bus_capacity: int = 10_000
    cors_origins: str = "http://localhost:5173"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
