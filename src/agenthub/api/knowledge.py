"""Private knowledge-base resources; uploads return before parsing or model calls."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, Response, UploadFile
from sqlalchemy import select

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.identity.dependencies import CurrentPrincipal, DatabaseSession
from agenthub.knowledge import service
from agenthub.knowledge.models import Document, DocumentChunk, IngestionJob, KnowledgeBase
from agenthub.knowledge.schemas import (
    ChunkOutput,
    CreateKnowledgeBase,
    DocumentOutput,
    JobOutput,
    KnowledgeBaseOutput,
)
from agenthub.knowledge.storage import file_path, validate_filename

router = APIRouter(
    prefix="/api/v1/knowledge-bases",
    tags=["knowledge"],
    responses={c: {"model": ErrorResponse} for c in (401, 404, 409, 413, 415, 422, 503)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.post("", response_model=KnowledgeBaseOutput, status_code=201)
def create_kb(
    data: CreateKnowledgeBase,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> KnowledgeBase:
    request.app.state.embedding_provider.check_configured()
    return service.create_kb(session, principal.user.id, data, request.app.state.settings)


@router.get("", response_model=list[KnowledgeBaseOutput])
def list_kbs(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[KnowledgeBase]:
    return list(
        session.scalars(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.owner_id == principal.user.id,
                KnowledgeBase.deleted_at.is_(None),
            )
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/{kb_id}", response_model=KnowledgeBaseOutput)
def get_kb(kb_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> KnowledgeBase:
    return service.owned_kb(session, kb_id, principal.user.id)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(
    kb_id: UUID, request: Request, session: DatabaseSession, principal: CurrentPrincipal
) -> Response:
    service.delete_kb(session, kb_id, principal.user.id)
    request.app.state.knowledge_queue.notify()
    return Response(status_code=204)


@router.post("/{kb_id}/documents", response_model=DocumentOutput, status_code=202)
def upload_document(
    kb_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File()],
) -> Document:
    service.owned_kb(session, kb_id, principal.user.id)
    filename, kind = validate_filename(file.filename)
    settings = request.app.state.settings
    content = file.file.read(settings.document_max_bytes + 1)
    doc = service.upload(
        session,
        kb_id,
        principal.user.id,
        filename,
        kind,
        content,
        settings,
        request.state.request_id,
    )
    request.app.state.knowledge_queue.notify()
    return doc


@router.get("/{kb_id}/documents", response_model=list[DocumentOutput])
def list_documents(
    kb_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[Document]:
    service.owned_kb(session, kb_id, principal.user.id)
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.knowledge_base_id == kb_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/{kb_id}/documents/{doc_id}", response_model=DocumentOutput)
def get_document(
    kb_id: UUID, doc_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> Document:
    return service.owned_document(session, kb_id, doc_id, principal.user.id)


@router.get("/{kb_id}/documents/{doc_id}/source")
def get_source(
    kb_id: UUID,
    doc_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> Response:
    doc = service.owned_document(session, kb_id, doc_id, principal.user.id, lock=True)
    try:
        content = file_path(request.app.state.settings.document_storage_path, doc.id).read_bytes()
    except OSError:
        raise AppError(404, "document_source_missing", "Document source is unavailable") from None
    # Read under the resource lock; never defer a FileResponse read beyond deletion.
    session.rollback()
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="document.' + doc.file_type + '"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(
    kb_id: UUID,
    doc_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> Response:
    service.delete_document(session, kb_id, doc_id, principal.user.id)
    request.app.state.knowledge_queue.notify()
    return Response(status_code=204)


@router.post("/{kb_id}/documents/{doc_id}/retry", response_model=JobOutput, status_code=202)
def retry_document(
    kb_id: UUID,
    doc_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> IngestionJob:
    job = service.retry(
        session,
        kb_id,
        doc_id,
        principal.user.id,
        request.app.state.settings,
        request.state.request_id,
    )
    request.app.state.knowledge_queue.notify()
    return job


@router.get("/{kb_id}/documents/{doc_id}/jobs", response_model=list[JobOutput])
def list_jobs(
    kb_id: UUID,
    doc_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[IngestionJob]:
    service.owned_document(session, kb_id, doc_id, principal.user.id)
    return list(
        session.scalars(
            select(IngestionJob)
            .where(IngestionJob.document_id == doc_id)
            .order_by(IngestionJob.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/{kb_id}/documents/{doc_id}/chunks", response_model=list[ChunkOutput])
def list_chunks(
    kb_id: UUID,
    doc_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[DocumentChunk]:
    doc = service.owned_document(session, kb_id, doc_id, principal.user.id)
    if doc.status != "ready":
        return []
    return list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.position)
            .limit(limit)
            .offset(offset)
        )
    )
