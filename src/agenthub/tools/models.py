"""Persistent service pins, reviewed tool versions and minimal execution audit."""

from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.session import Base


class McpService(Base):
    __tablename__ = "mcp_services"
    __table_args__ = (
        CheckConstraint("transport IN ('unix_http', 'https')", name="ck_mcp_services_transport"),
    )

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    transport: Mapped[str] = mapped_column(String(12))
    target_digest: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool]


class ToolRegistration(Base):
    __tablename__ = "tool_registrations"
    __table_args__ = (
        CheckConstraint("version BETWEEN 1 AND 1000000", name="ck_tool_registrations_version"),
        CheckConstraint(
            "effect IN ('read_only', 'write', 'high')", name="ck_tool_registrations_effect"
        ),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 60", name="ck_tool_registrations_timeout"),
        Index("ix_tool_registrations_service_key", "service_key"),
    )

    source: ClassVar[Literal["mcp"]] = "mcp"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    service_key: Mapped[str] = mapped_column(ForeignKey("mcp_services.key", ondelete="RESTRICT"))
    remote_name: Mapped[str] = mapped_column(String(128))
    definition_digest: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(500))
    required_role: Mapped[str] = mapped_column(ForeignKey("roles.name", ondelete="RESTRICT"))
    effect: Mapped[str] = mapped_column(String(12))
    timeout_seconds: Mapped[int]
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool]


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL) OR "
            "(status IN ('completed', 'rejected', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL AND duration_ms >= 0 AND duration_ms IS NOT NULL "
            "AND ((status = 'completed') = (error_code IS NULL)))",
            name="ck_tool_calls_state",
        ),
        CheckConstraint(
            "(tool_name IS NULL) = (tool_version IS NULL)", name="ck_tool_calls_tool_identity"
        ),
        Index("ix_tool_calls_actor_created", "actor_id", "created_at"),
        Index("ix_tool_calls_request_id", "request_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    request_id: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(64))
    tool_version: Mapped[int | None]
    service_key: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12))
    error_code: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
