"""Resource ownership, short transactions and durable deletion tombstones."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.knowledge.models import Document, DocumentChunk, IngestionJob, KnowledgeBase
from agenthub.knowledge.schemas import CreateKnowledgeBase
from agenthub.knowledge.storage import save_file


def owned_kb(session: Session, kb_id: UUID, owner_id: UUID, *, lock: bool = False) -> KnowledgeBase:
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.owner_id == owner_id,
        KnowledgeBase.deleted_at.is_(None),
    )
    kb = session.scalar(query.with_for_update() if lock else query)
    if kb is None:
        raise AppError(404, "knowledge_base_not_found", "Knowledge base not found")
    return kb


def owned_document(
    session: Session, kb_id: UUID, doc_id: UUID, owner_id: UUID, *, lock: bool = False
) -> Document:
    owned_kb(session, kb_id, owner_id, lock=lock)
    query = select(Document).where(
        Document.id == doc_id,
        Document.knowledge_base_id == kb_id,
        Document.deleted_at.is_(None),
    )
    doc = session.scalar(query.with_for_update() if lock else query)
    if doc is None:
        raise AppError(404, "document_not_found", "Document not found")
    return doc


def compatible(kb: KnowledgeBase, settings: Settings) -> None:
    if (
        kb.embedding_model != settings.embedding_model
        or kb.embedding_base_url != settings.embedding_base_url
        or kb.dimensions != settings.embedding_dimensions
        or kb.pipeline_version != "text-v1-char-v1-l2-v1"
        or kb.chunk_size > settings.embedding_max_characters
    ):
        raise AppError(409, "embedding_space_mismatch", "Create a new compatible knowledge base")


def create_kb(
    session: Session, owner_id: UUID, data: CreateKnowledgeBase, settings: Settings
) -> KnowledgeBase:
    if not settings.embedding_model or not settings.embedding_base_url:
        raise AppError(503, "embedding_not_configured", "Embedding service is not configured")
    if settings.embedding_dimensions != 1024 or data.chunk_size > settings.embedding_max_characters:
        raise AppError(422, "unsupported_embedding_configuration", "P4 requires 1024 dimensions")
    kb = KnowledgeBase(
        owner_id=owner_id,
        name=data.name.strip(),
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions,
        chunk_size=data.chunk_size,
        chunk_overlap=data.chunk_overlap,
    )
    session.add(kb)
    session.commit()
    return kb


def upload(
    session: Session,
    kb_id: UUID,
    owner_id: UUID,
    filename: str,
    kind: str,
    content: bytes,
    settings: Settings,
    request_id: str,
) -> Document:
    kb = owned_kb(session, kb_id, owner_id, lock=True)
    compatible(kb, settings)
    if not content:
        raise AppError(422, "empty_document", "Document is empty")
    if len(content) > settings.document_max_bytes:
        raise AppError(413, "document_size_limit", "Document too large")
    checksum = hashlib.sha256(content).hexdigest()
    if session.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == kb_id,
            Document.checksum == checksum,
            Document.deleted_at.is_(None),
        )
    ):
        raise AppError(409, "duplicate_document", "Identical content already exists")
    doc_id = uuid4()
    doc = Document(
        id=doc_id,
        knowledge_base_id=kb_id,
        filename=filename,
        file_type=kind,
        checksum=checksum,
        size_bytes=len(content),
        version=1,
    )
    try:
        save_file(settings.document_storage_path, doc_id, content)
        session.add(doc)
        session.flush()
        session.add(IngestionJob(document_id=doc_id, version=1, request_id=request_id))
        session.commit()
    except Exception:
        session.rollback()
        # Do not remove a committed file on an ambiguous COMMIT/network failure.
        # Unreferenced files are collected later after a grace period.
        raise
    return doc


def retry(
    session: Session, kb_id: UUID, doc_id: UUID, owner_id: UUID, settings: Settings, request_id: str
) -> IngestionJob:
    doc = owned_document(session, kb_id, doc_id, owner_id, lock=True)
    kb = session.get(KnowledgeBase, kb_id)
    assert kb is not None
    compatible(kb, settings)
    if doc.status != "failed":
        raise AppError(409, "document_not_failed", "Only failed documents can be retried")
    doc.version += 1
    doc.status, doc.error_code = "queued", None
    job = IngestionJob(document_id=doc.id, version=doc.version, request_id=request_id)
    session.add(job)
    session.commit()
    return job


def mark_deleted(session: Session, documents: list[Document]) -> None:
    now = datetime.now(UTC)
    for doc in documents:
        doc.deleted_at, doc.status, doc.chunk_count = now, "deleted", 0
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
        session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.document_id == doc.id,
                IngestionJob.status.in_(["queued", "processing"]),
            )
            .values(status="cancelled", finished_at=now, lease_until=None)
        )


def delete_document(session: Session, kb_id: UUID, doc_id: UUID, owner_id: UUID) -> None:
    doc = owned_document(session, kb_id, doc_id, owner_id, lock=True)
    mark_deleted(session, [doc])
    session.commit()


def delete_kb(session: Session, kb_id: UUID, owner_id: UUID) -> None:
    kb = owned_kb(session, kb_id, owner_id, lock=True)
    docs = list(
        session.scalars(
            select(Document)
            .where(
                Document.knowledge_base_id == kb_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.id)
            .with_for_update()
        )
    )
    mark_deleted(session, docs)
    kb.deleted_at = datetime.now(UTC)
    session.commit()
