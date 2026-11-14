"""Business state and replay ledger for versioned Agent workflows."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from agenthub.db.session import Base


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (Index("ix_agents_owner_created", "owner_id", "created_at", "id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'waiting_approval', "
            "'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "remaining_ms >= 0 AND event_seq >= 0 AND completion_tokens >= 0 "
            "AND prompt_tokens >= 0",
            name="ck_agent_runs_counters",
        ),
        CheckConstraint(
            "(status = 'running') = (active_since IS NOT NULL) "
            "AND (status = 'running') = (claim_id IS NOT NULL) "
            "AND (status = 'waiting_approval') = (approval_id IS NOT NULL) "
            "AND (status = 'failed') = (error_code IS NOT NULL) "
            "AND (status = 'completed') = (answer IS NOT NULL) "
            "AND (status = 'running' OR NOT pause_requested) "
            "AND (status = 'failed' OR NOT recoverable)",
            name="ck_agent_runs_state",
        ),
        Index("ix_agent_runs_owner_created", "owner_id", "created_at", "id"),
        Index("ix_agent_runs_worker", "status", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT")
    )
    approval_id: Mapped[UUID | None]  # Active interrupt pointer; approval belongs to a run step.
    claim_id: Mapped[UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_target: Mapped[str] = mapped_column(String(64))
    workflow_version: Mapped[int]
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    pause_requested: Mapped[bool]
    recoverable: Mapped[bool]
    remaining_ms: Mapped[int]
    completion_tokens: Mapped[int]
    prompt_tokens: Mapped[int]
    event_seq: Mapped[int]
    answer: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_agent_steps_run_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_agent_steps_ordinal"),
        CheckConstraint("kind IN ('model', 'tool')", name="ck_agent_steps_kind"),
        CheckConstraint(
            "status IN ('planned', 'started', 'completed', 'rejected', 'failed', 'uncertain')",
            name="ck_agent_steps_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="RESTRICT"))
    ordinal: Mapped[int]
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    tool_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentApproval(Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (
        CheckConstraint(
            "(decision IS NULL AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(decision IN ('approve', 'reject') AND decision IS NOT NULL "
            "AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_agent_approvals_decision",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    step_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="RESTRICT"), unique=True
    )
    digest: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(8))
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentEvent(Base):
    __tablename__ = "agent_events"

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
