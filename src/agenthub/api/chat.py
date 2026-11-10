"""Private chat endpoints. SSE always ends with done or error when connected."""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from agenthub.chat import service
from agenthub.chat.models import Conversation, Message
from agenthub.chat.provider import ChatProvider
from agenthub.chat.schemas import ConversationOutput, CreateConversation, MessageOutput, SendMessage
from agenthub.core.errors import AppError, ErrorResponse
from agenthub.identity.dependencies import CurrentPrincipal, DatabaseSession

router = APIRouter(
    prefix="/api/v1",
    tags=["chat"],
    responses={c: {"model": ErrorResponse} for c in (401, 404, 409, 422, 429, 502, 503, 504)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.get("/models")
def model_info(request: Request, principal: CurrentPrincipal) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "model": settings.model_name or None,
        "configured": bool(settings.model_base_url and settings.model_name),
        "capabilities": {
            "text": True,
            "streaming": True,
            "tool_calls": False,
            "structured_output": False,
        },
        "max_output_tokens": settings.model_max_tokens,
    }


@router.post("/conversations", response_model=ConversationOutput, status_code=201)
def create_conversation(
    data: CreateConversation, session: DatabaseSession, principal: CurrentPrincipal
) -> Conversation:
    conversation = Conversation(owner_id=principal.user.id, title=data.title)
    session.add(conversation)
    session.commit()
    return conversation


@router.get("/conversations", response_model=list[ConversationOutput])
def list_conversations(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[Conversation]:
    return list(
        session.scalars(
            select(Conversation)
            .where(Conversation.owner_id == principal.user.id)
            .order_by(Conversation.created_at.desc(), Conversation.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOutput])
def list_messages(
    conversation_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[Message]:
    service.owned(session, conversation_id, principal.user.id)
    return list(
        session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position)
            .limit(limit)
            .offset(offset)
        )
    )


def event(name: str, data: object) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def execute(
    request: Request,
    conversation_id: UUID,
    message_id: UUID,
    prompt: list[dict[str, str]],
    stream: bool,
) -> AsyncGenerator[str | MessageOutput]:
    started = time.monotonic()
    text = ""
    prompt_tokens = completion_tokens = None
    status = "cancelled"
    error_code: str | None = "generation_cancelled"
    provider: ChatProvider = request.app.state.chat_provider
    result: MessageOutput | None = None
    failure: AppError | None = None
    try:
        if stream:
            yield event("start", {"message_id": str(message_id)})
        async with asyncio.timeout(request.app.state.settings.model_timeout_seconds):
            async for chunk in provider.generate(prompt, stream=stream):
                text += chunk.text
                if len(text) > 200000:
                    raise AppError(502, "model_output_limit", "Model response too large")
                if chunk.prompt_tokens is not None:
                    prompt_tokens = chunk.prompt_tokens
                if chunk.completion_tokens is not None:
                    completion_tokens = chunk.completion_tokens
                if stream and chunk.text:
                    yield event("delta", {"message_id": str(message_id), "content": chunk.text})
        status, error_code = "completed", None
    except (AppError, TimeoutError) as exc:
        failure = (
            exc
            if isinstance(exc, AppError)
            else AppError(504, "model_timeout", "Model service timed out")
        )
        status, error_code = "failed", failure.code
    except Exception:
        failure = AppError(502, "generation_failed", "Generation failed")
        status, error_code = "failed", failure.code
    finally:
        # Starlette cancels the generator on disconnect. Shield the final short
        # DB transaction so partial output and cancellation remain queryable.
        with anyio.CancelScope(shield=True):
            result = await run_in_threadpool(
                service.finish,
                request.app.state.session_factory,
                conversation_id,
                message_id,
                text[:200000],
                status,
                error_code,
                int((time.monotonic() - started) * 1000),
                prompt_tokens,
                completion_tokens,
            )
    if failure:
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
    elif result is not None:
        yield event("done", result.model_dump(mode="json")) if stream else result


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOutput)
async def send_message(
    conversation_id: UUID,
    data: SendMessage,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    stream: bool = False,
) -> MessageOutput | StreamingResponse:
    provider: ChatProvider = request.app.state.chat_provider
    provider.check_configured()
    owner_id = principal.user.id
    # Release authentication's read transaction before waiting on the network.
    await run_in_threadpool(session.rollback)
    message_id, prompt = await run_in_threadpool(
        service.prepare,
        request.app.state.session_factory,
        conversation_id,
        owner_id,
        data.content,
        request.app.state.settings,
        request.state.request_id,
    )
    if stream:

        async def events() -> AsyncIterator[str]:
            async with aclosing(
                execute(request, conversation_id, message_id, prompt, True)
            ) as output:
                async for item in output:
                    assert isinstance(item, str)
                    yield item

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    async for item in execute(request, conversation_id, message_id, prompt, False):
        assert isinstance(item, MessageOutput)
        return item
    raise RuntimeError("Generation returned no result")
