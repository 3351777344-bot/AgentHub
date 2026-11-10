"""P4 tables. Deleted resources remain tombstones for late worker fencing."""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.session import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index("ix_knowledge_bases_owner_created", "owner_id", "created_at", "id"),
        CheckConstraint("dimensions = 1024", name="ck_kb_dimensions"),
        CheckConstraint(
            "chunk_size >= 100 AND chunk_size <= 4000 AND chunk_overlap >= 0 "
            "AND chunk_overlap < chunk_size",
            name="ck_kb_chunking",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    embedding_model: Mapped[str] = mapped_column(String(200))
    embedding_base_url: Mapped[str] = mapped_column(String(512))
    dimensions: Mapped[int]
    chunk_size: Mapped[int]
    chunk_overlap: Mapped[int]
    pipeline_version: Mapped[str] = mapped_column(String(64), default="text-v1-char-v1-l2-v1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_kb_created", "knowledge_base_id", "created_at", "id"),
        Index(
            "uq_documents_active_checksum",
            "knowledge_base_id",
            "checksum",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "status IN ('queued','processing','ready','failed','deleted')",
            name="ck_documents_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT")
    )
    filename: Mapped[str] = mapped_column(String(200))
    file_type: Mapped[str] = mapped_column(String(8))
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default="queued")
    version: Mapped[int] = mapped_column(default=1)
    chunk_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_ingestion_document_version"),
        CheckConstraint(
            "status IN ('queued','processing','completed','failed','cancelled')",
            name="ck_ingestion_status",
        ),
        Index("ix_ingestion_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(16), default="queued")
    request_id: Mapped[str] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    total_tokens: Mapped[int | None]
    completed_batches: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "position", name="uq_chunks_document_position"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="ck_chunks_offsets"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="RESTRICT"))
    position: Mapped[int]
    page_number: Mapped[int]
    char_start: Mapped[int]
    char_end: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(1024))
