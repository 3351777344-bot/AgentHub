"""Immutable datasets and configuration snapshots, durable runs and case reports."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.session import Base
from agenthub.evaluations.schemas import CaseStatus


class EvaluationDataset(Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", "version", name="uq_evaluation_dataset_version"),
        Index("ix_evaluation_datasets_owner_created", "owner_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    corpus_digest: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_evaluation_runs_status",
        ),
        CheckConstraint(
            "(status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL) "
            "AND (finished_at IS NOT NULL) = (report IS NOT NULL) "
            "AND (status = 'failed') = (error_code IS NOT NULL) "
            "AND (status <> 'running' OR started_at IS NOT NULL) "
            "AND reserved_tokens >= 0",
            name="ck_evaluation_runs_state",
        ),
        Index("ix_evaluation_runs_owner_created", "owner_id", "created_at", "id"),
        Index("ix_evaluation_runs_worker", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT")
    )
    dataset_digest: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_digest: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(64))
    reserved_tokens: Mapped[int] = mapped_column(default=0)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_evaluation_result_ordinal"),
        CheckConstraint(
            "status IN ('queued','running','passed','failed','skipped') AND ordinal > 0",
            name="ck_evaluation_results_state",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    ordinal: Mapped[int]
    status: Mapped[CaseStatus] = mapped_column(String(12), default="queued")
    failure_stage: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), unique=True
    )
    observation: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    score: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
