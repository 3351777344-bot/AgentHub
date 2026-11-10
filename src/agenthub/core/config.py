"""Validated environment configuration; secrets are excluded from repr."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also contains Compose-only variables.
        hide_input_in_errors=True,
        frozen=True,
    )

    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_host: str = Field(default="127.0.0.1", min_length=1)
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="agenthub", min_length=1)
    database_user: str = Field(default="agenthub", min_length=1)
    database_password: SecretStr = Field(default=SecretStr(""), repr=False)
    redis_url: SecretStr = Field(default=SecretStr("redis://127.0.0.1:6379/0"), repr=False)
    dependency_timeout_seconds: float = Field(default=3, ge=0.1, le=30)
    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    database_pool_size: int = Field(default=5, ge=1, le=20)

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        try:
            parsed = urlsplit(value.get_secret_value())
            if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("Expected a valid redis:// or rediss:// URL") from None
        return value
