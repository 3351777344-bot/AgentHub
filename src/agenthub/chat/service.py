"""Short transactions surround a model call; a lease serializes conversation turns."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agenthub.chat.models import Conversation, Message, ModelCall
from agenthub.chat.schemas import MessageOutput
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.rag.models import RagRun


def owned(
    session: Session, conversation_id: UUID, owner_id: UUID, *, lock: bool = False
) -> Conversation:
    query = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.owner_id == owner_id
    )
    if lock:
        query = query.with_for_update()
    conversation = session.scalar(query)
    if conversation is None:
        raise AppError(404, "conversation_not_found", "Conversation not found")
    return conversation


def prepare(
    factory: sessionmaker[Session],
    conversation_id: UUID,
    owner_id: UUID,
    content: str,
    settings: Settings,
    request_id: str,
) -> tuple[UUID, list[dict[str, str]]]:
    with factory() as session:
        conversation = owned(session, conversation_id, owner_id, lock=True)
        now = available_since(session, conversation)
        # Only successful user/assistant pairs enter the next prompt. Limit by
        # characters, not an invented tokenizer estimate; reject rather than silently truncate.
        history = list(
            session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.status == "completed",
                    ~select(RagRun.message_id).where(RagRun.message_id == Message.id).exists(),
                )
                .order_by(Message.position)
            )
        )
        successful_positions = {m.position - 1 for m in history if m.role == "assistant"}
        prompt = [
            {"role": m.role, "content": m.content}
            for m in history
            if m.role == "assistant" or m.position in successful_positions
        ]
        prompt.append({"role": "user", "content": content})
        if sum(len(m["content"]) for m in prompt) > settings.model_context_characters:
            raise AppError(
                422, "context_limit_exceeded", "Start a new conversation: context limit exceeded"
            )
        assistant = reserve_turn(
            session, conversation, content, now, settings.model_timeout_seconds
        )
        session.add(
            ModelCall(
                assistant_message_id=assistant.id, model=settings.model_name, request_id=request_id
            )
        )
        session.commit()
        return assistant.id, prompt


def available_since(session: Session, conversation: Conversation) -> datetime:
    """Check and recover the lease while holding the conversation row lock."""
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    if conversation.busy_until and conversation.busy_until > now:
        raise AppError(409, "conversation_busy", "Conversation already has an active generation")
    pending = session.scalars(
        select(Message).where(
            Message.conversation_id == conversation.id, Message.status == "pending"
        )
    )
    for message in pending:
        message.status, message.error_code = "failed", "generation_interrupted"
    return now


def reserve_turn(
    session: Session, conversation: Conversation, content: str, now: datetime, seconds: float
) -> Message:
    user = Message(
        conversation_id=conversation.id,
        position=conversation.next_position,
        role="user",
        content=content,
        status="completed",
    )
    assistant = Message(
        conversation_id=conversation.id,
        position=conversation.next_position + 1,
        role="assistant",
        content="",
        status="pending",
    )
    session.add_all([user, assistant])
    session.flush()
    conversation.next_position += 2
    conversation.busy_until = now + timedelta(seconds=seconds + 30)
    return assistant


def lock_turn(
    session: Session, conversation_id: UUID, message_id: UUID
) -> tuple[Conversation, Message]:
    # Same lock ordering as prepare: conversation then message/call.
    conversation = session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    message = session.get(Message, message_id)
    assert conversation is not None and message is not None
    return conversation, message


def finish(
    factory: sessionmaker[Session],
    conversation_id: UUID,
    message_id: UUID,
    content: str,
    status: str,
    error_code: str | None,
    duration_ms: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> MessageOutput:
    with factory() as session:
        conversation, message = lock_turn(session, conversation_id, message_id)
        if message.status == "pending":
            message.content, message.status, message.error_code = content, status, error_code
            call = session.scalar(
                select(ModelCall).where(ModelCall.assistant_message_id == message_id)
            )
            assert call is not None
            call.duration_ms, call.prompt_tokens = duration_ms, prompt_tokens
            call.completion_tokens = completion_tokens
            conversation.busy_until = None
            session.commit()
        return MessageOutput.model_validate(message)
