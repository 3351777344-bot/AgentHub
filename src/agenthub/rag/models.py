"""Retrieval metadata keyed by the assistant message."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.session import Base


class RagRun(Base):
    __tablename__ = "rag_runs"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('answered', 'insufficient_evidence')", name="ck_rag_runs_outcome"
        ),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), primary_key=True
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT")
    )
    embedding_model: Mapped[str] = mapped_column(String(200))
    request_id: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(32))
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    embedding_ms: Mapped[int | None]
    embedding_tokens: Mapped[int | None]
    search_ms: Mapped[int | None]
    retrieved_count: Mapped[int | None]
    context_count: Mapped[int | None]
