"""Validated environment configuration; secrets are excluded from repr."""

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpRemoteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    url: str = Field(max_length=512)
    ip: IPvAnyAddress
    auth: Literal["none", "bearer"] = "none"


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
    model_base_url: str = ""
    model_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    model_name: str = Field(default="", max_length=200)
    model_timeout_seconds: float = Field(default=60, ge=1, le=300)
    model_max_tokens: int = Field(default=1024, ge=1, le=8192)
    model_context_characters: int = Field(default=24000, ge=1000, le=200000)
    model_include_usage: bool = False
    model_thinking_mode: Literal["provider_default", "disabled"] = "provider_default"
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    embedding_model: str = Field(default="", max_length=200)
    embedding_dimensions: int = Field(default=1024, ge=1, le=2560)
    embedding_timeout_seconds: float = Field(default=30, ge=1, le=120)
    embedding_batch_size: int = Field(default=10, ge=1, le=20)
    embedding_max_characters: int = Field(default=8000, ge=1, le=32000)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_min_similarity: float = Field(default=0.45, ge=0, le=1)
    rag_context_characters: int = Field(default=8000, ge=1, le=80000)
    mcp_local_socket: Path | None = None
    mcp_remote_targets: dict[str, McpRemoteTarget] = Field(default_factory=dict, repr=False)
    mcp_remote_tokens: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    mcp_remote_ca_file: Path | None = None
    document_storage_path: Path = Path("data/documents")
    document_max_bytes: int = Field(default=5_000_000, ge=1, le=20_000_000)
    document_max_characters: int = Field(default=200000, ge=1, le=1000000)
    document_max_chunks: int = Field(default=500, ge=1, le=2000)
    document_max_pages: int = Field(default=200, ge=1, le=1000)
    document_parse_timeout_seconds: float = Field(default=30, ge=1, le=120)
    worker_poll_seconds: int = Field(default=5, ge=1, le=30)

    @field_validator("embedding_base_url")
    @classmethod
    def validate_embedding_url(cls, value: str) -> str:
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not re.fullmatch(
                    r"[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", parsed.hostname or ""
                )
                or parsed.port not in (None, 443)
                or parsed.path.rstrip("/") != "/compatible-mode/v1"
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
        except ValueError:
            raise ValueError(
                "Expected a Beijing workspace HTTPS compatible-mode/v1 base URL"
            ) from None
        return value.rstrip("/")

    @field_validator("model_base_url")
    @classmethod
    def validate_model_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected an HTTP(S) base URL without credentials or query")
        return value.rstrip("/")

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
