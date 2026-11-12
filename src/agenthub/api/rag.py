"""Private retrieval and grounded conversation turns."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import UUID

import anyio
from fastapi import APIRouter, Request
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Send

from agenthub.api.chat import event
from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError, ErrorResponse
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.identity.dependencies import CurrentPrincipal, DatabaseSession
from agenthub.rag import retrieval, service
from agenthub.rag.schemas import (
    AskRequest,
    RagOutput,
    RetrievalStats,
    SearchHit,
    SearchOutput,
    SearchRequest,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["rag"],
    responses={c: {"model": ErrorResponse} for c in (401, 404, 409, 422, 429, 502, 503, 504)},
)
logger = logging.getLogger("agenthub.rag")
DATABASE_ERRORS = (OperationalError, InterfaceError, DatabaseTimeoutError)


class RagStreamingResponse(StreamingResponse):
    def __init__(self, events: AsyncGenerator[str]) -> None:
        self.events = events
        super().__init__(
            events, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # ASGI 2.4+ can report a disconnect by failing send between iterations.
            with anyio.CancelScope(shield=True):
                await self.events.aclose()


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def retrieve(
    request: Request, kb_id: UUID, owner_id: UUID, data: SearchRequest, stats: RetrievalStats
) -> list[SearchHit]:
    provider: EmbeddingProvider = request.app.state.embedding_provider
    factory: sessionmaker[Session] = request.app.state.session_factory
    settings: Settings = request.app.state.settings
    started = time.monotonic()
    try:
        embedding = await provider.embed([data.question])
    finally:
        stats.embedding_ms = elapsed_ms(started)
    stats.embedding_tokens = embedding.total_tokens

    def query() -> list[SearchHit]:
        with factory() as session:
            return retrieval.search(session, kb_id, owner_id, data, settings, embedding)

    started = time.monotonic()
    try:
        hits = await run_in_threadpool(query)
    finally:
        stats.search_ms = elapsed_ms(started)
    stats.retrieved_count = len(hits)
    return hits


@router.post("/knowledge-bases/{kb_id}/search", response_model=SearchOutput)
async def search(
    kb_id: UUID,
    data: SearchRequest,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> SearchOutput:
    owner_id = principal.user.id

    def verify() -> None:
        try:
            service.verify_scope(
                session, kb_id, owner_id, request.app.state.embedding_provider, data.question
            )
        finally:
            session.rollback()

    stats = RetrievalStats()
    error_code = None
    try:
        await run_in_threadpool(verify)
        hits = await retrieve(request, kb_id, owner_id, data, stats)
    except AppError as exc:
        error_code = exc.code
        raise
    except DATABASE_ERRORS:
        error_code = "database_unavailable"
        raise
    finally:
        logger.info("rag_search_finished", extra={**stats.model_dump(), "error_code": error_code})
    return SearchOutput(hits=hits, retrieval=stats)


async def execute(
    request: Request,
    conversation_id: UUID,
    message_id: UUID,
    owner_id: UUID,
    data: AskRequest,
    stream: bool,
) -> AsyncGenerator[str | RagOutput]:
    settings: Settings = request.app.state.settings
    provider: ChatProvider = request.app.state.chat_provider
    result = service.TurnResult()
    failure: AppError | None = None
    output = None
    try:
        if stream:
            yield event("start", {"message_id": str(message_id)})
        hits = await retrieve(request, data.knowledge_base_id, owner_id, data, result.retrieval)
        prompt, result.evidence = retrieval.prompt_for(data.question, hits, settings)
        result.retrieval.context_count = len(result.evidence)
        result.content, result.outcome = retrieval.NO_EVIDENCE, "insufficient_evidence"
        if result.evidence:
            provider.check_configured()
            started = time.monotonic()
            text, emitted = "", 0
            try:
                async with (
                    asyncio.timeout(settings.model_timeout_seconds),
                    aclosing(provider.generate(prompt, stream=stream)) as chunks,
                ):
                    async for chunk in chunks:
                        text += chunk.text
                        if len(text) > 200000:
                            raise AppError(502, "model_output_limit", "Model response too large")
                        if "\x00" in chunk.text:
                            raise AppError(502, "model_invalid_response", "Invalid model response")
                        try:
                            chunk.text.encode("utf-8")
                        except UnicodeEncodeError:
                            raise AppError(
                                502, "model_invalid_response", "Invalid model response"
                            ) from None
                        if chunk.prompt_tokens is not None:
                            result.prompt_tokens = chunk.prompt_tokens
                        if chunk.completion_tokens is not None:
                            result.completion_tokens = chunk.completion_tokens
                        if stream and chunk.text and not retrieval.ABSTAIN.startswith(text.strip()):
                            # Deltas are drafts. A client commits only the text in done.
                            yield event(
                                "delta", {"message_id": str(message_id), "content": text[emitted:]}
                            )
                            emitted = len(text)
                result.content, result.outcome, result.citations = retrieval.validate_answer(
                    text, result.evidence
                )
            finally:
                result.model_ms = elapsed_ms(started)
        result.status, result.error_code = "completed", None
    except TimeoutError:
        failure = AppError(504, "model_timeout", "Model service timed out")
        result.status, result.error_code = "failed", failure.code
    except AppError as exc:
        failure = exc
        result.status, result.error_code = "failed", exc.code
    except DATABASE_ERRORS:
        failure = AppError(503, "database_unavailable", "Database temporarily unavailable")
        result.status, result.error_code = "failed", failure.code
    except Exception:
        failure = AppError(502, "rag_generation_failed", "RAG request failed")
        result.status, result.error_code = "failed", failure.code
    finally:
        with anyio.CancelScope(shield=True):
            try:
                output = await run_in_threadpool(
                    service.finish,
                    request.app.state.session_factory,
                    conversation_id,
                    message_id,
                    owner_id,
                    settings.model_name,
                    result,
                )
            except SQLAlchemyError:
                failure = AppError(503, "rag_persistence_failed", "RAG result could not be saved")
            logger.info(
                "rag_turn_finished",
                extra={
                    **result.retrieval.model_dump(),
                    "model_ms": result.model_ms,
                    "error_code": failure.code if failure else result.error_code,
                },
            )
    if failure is None and output is not None and output.message.status != "completed":
        failure = AppError(
            409, output.message.error_code or "rag_incomplete", "RAG turn interrupted"
        )
    if failure is not None:
        if not stream:
            raise failure
        yield event(
            "error",
            {
                "message_id": str(message_id),
                "code": failure.code,
                "request_id": request.state.request_id,
            },
        )
    elif output is not None:
        yield event("done", output.model_dump(mode="json")) if stream else output


@router.post(
    "/conversations/{conversation_id}/rag",
    response_model=RagOutput,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def ask(
    conversation_id: UUID,
    data: AskRequest,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    stream: bool = False,
) -> RagOutput | StreamingResponse:
    owner_id = principal.user.id
    await run_in_threadpool(session.rollback)
    message_id = await run_in_threadpool(
        service.prepare,
        request.app.state.session_factory,
        conversation_id,
        owner_id,
        data,
        request.app.state.embedding_provider,
        request.app.state.settings,
        request.state.request_id,
    )
    if stream:

        async def events() -> AsyncGenerator[str]:
            async with aclosing(
                execute(request, conversation_id, message_id, owner_id, data, True)
            ) as items:
                async for item in items:
                    assert isinstance(item, str)
                    yield item

        return RagStreamingResponse(events())
    async with aclosing(
        execute(request, conversation_id, message_id, owner_id, data, False)
    ) as items:
        result = await anext(items)
    assert isinstance(result, RagOutput)
    return result


@router.get("/conversations/{conversation_id}/messages/{message_id}/rag", response_model=RagOutput)
def history(
    conversation_id: UUID,
    message_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> RagOutput:
    return service.history(session, conversation_id, message_id, principal.user.id)
