"""Shared grounded generation for HTTP turns and evaluation cases."""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.rag import retrieval
from agenthub.rag.schemas import AskRequest
from agenthub.rag.search import retrieve
from agenthub.rag.service import TurnResult


async def generate(
    factory: sessionmaker[Session],
    provider: ChatProvider,
    embeddings: EmbeddingProvider,
    settings: Settings,
    owner_id: UUID,
    data: AskRequest,
    result: TurnResult,
    *,
    stream: bool = False,
    verify: bool = False,
) -> AsyncGenerator[str]:
    result.stage = "retrieval"
    hits = await retrieve(
        factory,
        embeddings,
        settings,
        data.knowledge_base_id,
        owner_id,
        data,
        result.retrieval,
        verify=verify,
    )
    result.retrieved = [hit.source.chunk_id for hit in hits]
    prompt, result.evidence = retrieval.prompt_for(data.question, hits, settings)
    result.retrieval.context_count = len(result.evidence)
    if not result.evidence:
        result.content, result.outcome = retrieval.NO_EVIDENCE, "insufficient_evidence"
    else:
        result.stage = "model"
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
                    try:
                        chunk.text.encode("utf-8")
                        if "\x00" in chunk.text:
                            raise ValueError
                    except ValueError:
                        raise AppError(
                            502, "model_invalid_response", "Invalid model response"
                        ) from None
                    if chunk.prompt_tokens is not None:
                        result.prompt_tokens = chunk.prompt_tokens
                    if chunk.completion_tokens is not None:
                        result.completion_tokens = chunk.completion_tokens
                    if stream and chunk.text and not retrieval.ABSTAIN.startswith(text.strip()):
                        # SSE drafts become final only when the HTTP turn commits.
                        yield text[emitted:]
                        emitted = len(text)
            result.content, result.outcome, result.citations = retrieval.validate_answer(
                text, result.evidence
            )
        except TimeoutError:
            raise AppError(504, "model_timeout", "Model service timed out") from None
        finally:
            result.model_ms = int((time.monotonic() - started) * 1000)
    result.status, result.error_code = "completed", None
