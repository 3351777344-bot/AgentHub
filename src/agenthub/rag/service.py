"""RAG persistence and ownership checks at each database boundary."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agenthub.chat import service as chat
from agenthub.chat.models import Message, ModelCall
from agenthub.chat.schemas import MessageOutput
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.knowledge.service import compatible, owned_kb
from agenthub.rag.models import RagRun
from agenthub.rag.retrieval import available_chunks, prompt_for
from agenthub.rag.schemas import AskRequest, Citation, Outcome, RagOutput, RetrievalStats


@dataclass
class TurnResult:
    status: Literal["completed", "failed", "cancelled"] = "cancelled"
    error_code: str | None = "generation_cancelled"
    outcome: Outcome | None = None
    content: str = ""
    evidence: list[Citation] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    retrieval: RetrievalStats = field(default_factory=RetrievalStats)
    stage: Literal["retrieval", "model"] = "retrieval"
    retrieved: list[UUID] = field(default_factory=list)
    model_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def verify_scope(
    session: Session, kb_id: UUID, owner_id: UUID, provider: EmbeddingProvider, question: str
) -> None:
    kb = owned_kb(session, kb_id, owner_id)
    provider.check_configured()
    compatible(kb, provider.settings)
    if len(question) > provider.settings.embedding_max_characters:
        raise AppError(422, "embedding_invalid_input", "Question exceeds the embedding input limit")


def prepare(
    factory: sessionmaker[Session],
    conversation_id: UUID,
    owner_id: UUID,
    data: AskRequest,
    provider: EmbeddingProvider,
    settings: Settings,
    request_id: str,
) -> UUID:
    with factory() as session:
        conversation = chat.owned(session, conversation_id, owner_id, lock=True)
        verify_scope(session, data.knowledge_base_id, owner_id, provider, data.question)
        prompt_for(data.question, [], settings)
        now = chat.available_since(session, conversation)
        assistant = chat.reserve_turn(
            session,
            conversation,
            data.question,
            now,
            settings.embedding_timeout_seconds + settings.model_timeout_seconds,
        )
        session.add(
            RagRun(
                message_id=assistant.id,
                knowledge_base_id=data.knowledge_base_id,
                request_id=request_id,
                embedding_model=settings.embedding_model,
            )
        )
        session.commit()
        return assistant.id


def output(session: Session, message: Message, run: RagRun, owner_id: UUID) -> RagOutput:
    citations = [Citation.model_validate(data) for data in run.citations]
    live = available_chunks(
        session, run.knowledge_base_id, owner_id, [item.source.chunk_id for item in citations]
    )
    for item in citations:
        item.available = item.source.chunk_id in live
    return RagOutput.model_validate(
        {
            "message": MessageOutput.model_validate(message),
            "knowledge_base_id": run.knowledge_base_id,
            "outcome": run.outcome,
            "citations": citations,
            "retrieval": RetrievalStats.model_validate(run, from_attributes=True),
        }
    )


def finish(
    factory: sessionmaker[Session],
    conversation_id: UUID,
    message_id: UUID,
    owner_id: UUID,
    model: str,
    result: TurnResult,
) -> RagOutput:
    with factory() as session:
        conversation, message = chat.lock_turn(session, conversation_id, message_id)
        run = session.get(RagRun, message_id)
        assert run is not None
        if message.status != "pending":
            return output(session, message, run, owner_id)

        if result.status == "completed":
            # P4 deletion also locks the KB first. Fence final publication against
            # deletion without holding the lock across either upstream call.
            chunk_ids = [item.source.chunk_id for item in result.evidence]
            try:
                owned_kb(session, run.knowledge_base_id, owner_id, lock=True)
                live = available_chunks(session, run.knowledge_base_id, owner_id, chunk_ids)
            except AppError:
                live = None
            if live != set(chunk_ids):
                result.status, result.error_code = "failed", "rag_source_unavailable"

        message.status, message.error_code = result.status, result.error_code
        if result.status == "completed":
            message.content = result.content
            run.outcome = result.outcome
            run.citations = [
                item.model_dump(mode="json", exclude={"available"}) for item in result.citations
            ]
        run.embedding_ms = result.retrieval.embedding_ms
        run.embedding_tokens = result.retrieval.embedding_tokens
        run.search_ms = result.retrieval.search_ms
        run.retrieved_count = result.retrieval.retrieved_count
        run.context_count = result.retrieval.context_count
        if result.model_ms is not None:
            session.add(
                ModelCall(
                    assistant_message_id=message_id,
                    request_id=run.request_id,
                    model=model,
                    duration_ms=result.model_ms,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                )
            )
        conversation.busy_until = None
        response = output(session, message, run, owner_id)
        session.commit()
        return response


def history(session: Session, conversation_id: UUID, message_id: UUID, owner_id: UUID) -> RagOutput:
    chat.owned(session, conversation_id, owner_id)
    pair = session.execute(
        select(Message, RagRun)
        .join(RagRun, RagRun.message_id == Message.id)
        .where(Message.id == message_id, Message.conversation_id == conversation_id)
    ).one_or_none()
    if pair is None:
        raise AppError(404, "rag_message_not_found", "RAG message not found")
    message, run = pair
    return output(session, message, run, owner_id)
