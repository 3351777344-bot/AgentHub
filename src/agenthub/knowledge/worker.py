"""Independent ingestion worker: durable claiming, explicit recovery, atomic publication."""

import argparse
import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.core.logging import configure_logging
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.identity import models as identity_models  # noqa: F401
from agenthub.identity.models import User
from agenthub.knowledge.models import Document, DocumentChunk, IngestionJob, KnowledgeBase
from agenthub.knowledge.parsing import Chunk, parse_file, split_sections
from agenthub.knowledge.queue import WakeupQueue
from agenthub.knowledge.service import compatible
from agenthub.knowledge.storage import file_path

logger = logging.getLogger("agenthub.ingestion")
Factory = sessionmaker[Session]


def lease(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(
        seconds=(settings.document_parse_timeout_seconds + settings.embedding_timeout_seconds + 60)
    )


def locked(session: Session, job_id: UUID) -> tuple[KnowledgeBase, Document, IngestionJob]:
    # Every multi-resource mutation locks KB -> document -> job, including delete.
    doc_id = session.scalar(select(IngestionJob.document_id).where(IngestionJob.id == job_id))
    kb_id = session.scalar(select(Document.knowledge_base_id).where(Document.id == doc_id))
    kb = session.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update())
    doc = session.scalar(select(Document).where(Document.id == doc_id).with_for_update())
    job = session.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
    assert kb is not None and doc is not None and job is not None
    return kb, doc, job


def live(kb: KnowledgeBase, doc: Document, job: IngestionJob) -> bool:
    return (
        kb.deleted_at is None
        and doc.deleted_at is None
        and doc.version == job.version
        and job.status == "processing"
    )


def claim(factory: Factory, settings: Settings) -> UUID | None:
    with factory.begin() as session:
        # This transaction touches only job rows, never waits on a KB/document.
        job = session.scalar(
            select(IngestionJob)
            .where(IngestionJob.status == "queued")
            .order_by(IngestionJob.created_at, IngestionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status, job.lease_until = "processing", lease(settings)
        return job.id


def fail(factory: Factory, job_id: UUID, code: str) -> None:
    with factory.begin() as session:
        kb, doc, job = locked(session, job_id)
        if not live(kb, doc, job):
            return
        job.status, job.error_code = "failed", code
        job.finished_at, job.lease_until = datetime.now(UTC), None
        doc.status, doc.error_code = "failed", code


def recover(factory: Factory) -> int:
    with factory() as session:
        expired = list(
            session.scalars(
                select(IngestionJob.id)
                .where(
                    IngestionJob.status == "processing",
                    IngestionJob.lease_until < datetime.now(UTC),
                )
                .limit(100)
            )
        )
    count = 0
    for job_id in expired:
        with factory.begin() as session:
            kb, doc, job = locked(session, job_id)
            # Re-check after locks: another worker may have renewed this lease.
            if (
                live(kb, doc, job)
                and job.lease_until is not None
                and job.lease_until < datetime.now(UTC)
            ):
                job.status, job.error_code = "failed", "worker_interrupted"
                job.finished_at, job.lease_until = datetime.now(UTC), None
                doc.status, doc.error_code = "failed", "worker_interrupted"
                count += 1
    return count


def progress(
    factory: Factory,
    job_id: UUID,
    settings: Settings,
    *,
    tokens: int | None = None,
    batch_completed: bool = False,
) -> bool:
    with factory.begin() as session:
        kb, doc, job = locked(session, job_id)
        if not live(kb, doc, job):
            return False
        if job.lease_until is None or job.lease_until < datetime.now(UTC):
            raise AppError(409, "worker_interrupted", "Worker lease expired")
        user = session.get(User, kb.owner_id)
        if user is None or not user.is_active:
            raise AppError(403, "owner_inactive", "Resource owner is inactive")
        compatible(kb, settings)
        doc.status = "processing"
        job.lease_until = lease(settings)
        if batch_completed:
            job.total_tokens = (
                tokens
                if job.completed_batches == 0
                else (
                    job.total_tokens + tokens
                    if job.total_tokens is not None and tokens is not None
                    else None
                )
            )
            job.completed_batches += 1
        return True


def publish(
    factory: Factory,
    job_id: UUID,
    chunks: list[Chunk],
    vectors: list[list[float]],
    settings: Settings,
) -> bool:
    with factory.begin() as session:
        kb, doc, job = locked(session, job_id)
        if not live(kb, doc, job):
            return False
        if job.lease_until is None or job.lease_until < datetime.now(UTC):
            raise AppError(409, "worker_interrupted", "Worker lease expired")
        compatible(kb, settings)
        user = session.get(User, kb.owner_id)
        if user is None or not user.is_active:
            raise AppError(403, "owner_inactive", "Resource owner is inactive")
        for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    job_id=job.id,
                    position=position,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    content=chunk.content,
                    embedding=vector,
                )
            )
        doc.status, doc.chunk_count, doc.error_code = "ready", len(chunks), None
        job.status, job.finished_at, job.lease_until = "completed", datetime.now(UTC), None
    return True


async def process(
    factory: Factory, job_id: UUID, settings: Settings, provider: EmbeddingProvider
) -> None:
    try:
        if not progress(factory, job_id, settings):
            return
        with factory() as session:
            job = session.get(IngestionJob, job_id)
            assert job is not None
            doc = session.get(Document, job.document_id)
            assert doc is not None
            kb = session.get(KnowledgeBase, doc.knowledge_base_id)
            assert kb is not None
            request_id = job.request_id
        logger.info("ingestion_started", extra={"job_id": str(job_id), "request_id": request_id})
        path = file_path(settings.document_storage_path, doc.id)
        with path.open("rb") as source:
            raw = source.read(settings.document_max_bytes + 1)
        if len(raw) > settings.document_max_bytes:
            raise AppError(413, "document_size_limit", "Document too large")
        if hashlib.sha256(raw).hexdigest() != doc.checksum:
            raise AppError(422, "document_checksum_mismatch", "Source integrity check failed")
        sections = parse_file(path, doc.file_type, settings)
        chunks = split_sections(
            sections, kb.chunk_size, kb.chunk_overlap, settings.document_max_chunks
        )
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), settings.embedding_batch_size):
            if not progress(factory, job_id, settings):
                return
            batch = chunks[start : start + settings.embedding_batch_size]
            result = await provider.embed([chunk.content for chunk in batch])
            if (
                result.model != kb.embedding_model
                or result.dimensions != kb.dimensions
                or len(result.vectors) != len(batch)
            ):
                raise AppError(502, "embedding_space_mismatch", "Embedding space mismatch")
            if not progress(
                factory, job_id, settings, tokens=result.total_tokens, batch_completed=True
            ):
                return
            vectors.extend(result.vectors)
        published = publish(factory, job_id, chunks, vectors, settings)
        logger.info(
            "ingestion_finished",
            extra={"job_id": str(job_id), "request_id": request_id, "published": published},
        )
    except AppError as error:
        fail(factory, job_id, error.code)
        logger.warning("ingestion_failed", extra={"job_id": str(job_id), "error_code": error.code})
    except FileNotFoundError:
        fail(factory, job_id, "document_source_missing")
    except Exception:
        fail(factory, job_id, "ingestion_failed")
        logger.error("ingestion_failed", extra={"job_id": str(job_id)})


def cleanup(factory: Factory, settings: Settings) -> int:
    """Retry physical deletion; also collect unreferenced raw/partial files after one hour."""
    removed = 0
    with factory() as session:
        ids = list(
            session.scalars(
                select(Document.id)
                .where(
                    Document.deleted_at.is_not(None),
                    Document.file_removed_at.is_(None),
                )
                .limit(100)
            )
        )
    for doc_id in ids:
        try:
            file_path(settings.document_storage_path, doc_id).unlink(missing_ok=True)
        except OSError:
            continue
        with factory.begin() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            doc.file_removed_at = datetime.now(UTC)
        removed += 1
    root = settings.document_storage_path
    if root.exists():
        for path in root.iterdir():
            if path.suffix not in {".raw", ".part"} or len(path.stem) != 32:
                continue
            try:
                doc_id = UUID(hex=path.stem)
                if path.stat().st_mtime > time.time() - 3600:
                    continue
                with factory() as session:
                    referenced = session.get(Document, doc_id) is not None
                if not referenced:
                    path.unlink(missing_ok=True)
                    removed += 1
            except (ValueError, OSError):
                continue
    return removed


async def execute_job(factory: Factory, job_id: UUID, settings: Settings) -> None:
    async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as http:
        await process(factory, job_id, settings, EmbeddingProvider(settings, http))


def run(*, once: bool = False) -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    queue = WakeupQueue(settings)
    try:
        while True:
            try:
                recover(factory)
                cleanup(factory, settings)
                job_id = claim(factory, settings)
                if job_id is not None:
                    asyncio.run(execute_job(factory, job_id, settings))
                elif not once:
                    queue.wait(settings.worker_poll_seconds)
                if once:
                    return
            except Exception:
                logger.error("ingestion_worker_cycle_failed")
                if once:
                    raise
                time.sleep(settings.worker_poll_seconds)
    finally:
        queue.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentHub document ingestion worker")
    parser.add_argument("--once", action="store_true", help="Run one recovery/claim cycle and exit")
    run(once=parser.parse_args().once)
