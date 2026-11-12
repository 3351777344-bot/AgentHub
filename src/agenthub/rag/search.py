"""Shared embedding and private pgvector search for HTTP and registered tools."""

import time
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from agenthub.core.config import Settings
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.rag import retrieval, service
from agenthub.rag.schemas import RetrievalStats, SearchHit, SearchRequest


async def retrieve(
    factory: sessionmaker[Session],
    provider: EmbeddingProvider,
    settings: Settings,
    kb_id: UUID,
    owner_id: UUID,
    data: SearchRequest,
    stats: RetrievalStats,
    *,
    verify: bool = False,
) -> list[SearchHit]:
    if verify:

        def verify_scope() -> None:
            with factory() as session:
                service.verify_scope(session, kb_id, owner_id, provider, data.question)

        await run_in_threadpool(verify_scope)

    started = time.monotonic()
    try:
        embedding = await provider.embed([data.question])
    finally:
        stats.embedding_ms = int((time.monotonic() - started) * 1000)
    stats.embedding_tokens = embedding.total_tokens

    def query() -> list[SearchHit]:
        with factory() as session:
            return retrieval.search(session, kb_id, owner_id, data, settings, embedding)

    started = time.monotonic()
    try:
        hits = await run_in_threadpool(query)
    finally:
        stats.search_ms = int((time.monotonic() - started) * 1000)
    stats.retrieved_count = len(hits)
    return hits
