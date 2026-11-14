"""Application factory: uvicorn agenthub.main:create_app --factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from agenthub import __version__
from agenthub.api.agents import router as agents_router
from agenthub.api.chat import router as chat_router
from agenthub.api.evaluations import router as evaluations_router
from agenthub.api.health import router as health_router
from agenthub.api.identity import router as identity_router
from agenthub.api.knowledge import router as knowledge_router
from agenthub.api.rag import router as rag_router
from agenthub.api.tools import router as tools_router
from agenthub.chat.provider import ChatProvider
from agenthub.core.body_limit import BodyLimitMiddleware
from agenthub.core.config import Settings
from agenthub.core.dependencies import DependencyChecks
from agenthub.core.errors import ErrorResponse, register_error_handlers
from agenthub.core.logging import configure_logging
from agenthub.core.middleware import RequestContextMiddleware
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.knowledge.queue import WakeupQueue
from agenthub.tools.contracts import ARGUMENT_BYTES
from agenthub.web import attach_console

logger = logging.getLogger("agenthub.lifecycle")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)
    # The SDK can log upstream message bodies. Application logs use only public error codes.
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        dependencies = DependencyChecks(settings)
        app.state.dependencies = dependencies
        engine = create_database_engine(settings)
        app.state.session_factory = create_session_factory(engine)
        model_client = httpx2.AsyncClient(follow_redirects=False, trust_env=False)
        app.state.chat_provider = ChatProvider(settings, model_client)
        app.state.embedding_provider = EmbeddingProvider(settings, model_client)
        app.state.knowledge_queue = WakeupQueue(settings)
        try:
            # Report startup dependency failures, but keep liveness and diagnostics available.
            checks = await dependencies.check()
            logger.info("application_started", extra={"checks": checks})
            yield
        finally:
            await model_client.aclose()
            await run_in_threadpool(app.state.knowledge_queue.close)
            await run_in_threadpool(engine.dispose)
            await dependencies.aclose()
            logger.info("application_stopped")

    app = FastAPI(
        title="AgentHub",
        version=__version__,
        debug=False,
        lifespan=lifespan,
        responses={
            422: {"model": ErrorResponse, "description": "Request validation failed"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.document_max_bytes + 65536,
        prefixes=("/api/v1/knowledge-bases/",),
        methods=("POST",),
        code="document_size_limit",
        detail="Upload too large",
    )
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=ARGUMENT_BYTES * 2 + 1024,
        prefixes=("/api/v1/tools/", "/api/v1/mcp/services", "/api/v1/tool-registrations"),
        methods=("POST", "PATCH"),
        code="tool_request_too_large",
        detail="Tool request too large",
    )
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=131072,
        prefixes=("/api/v1/agents", "/api/v1/agent-runs"),
        methods=("POST", "PUT"),
        code="agent_request_too_large",
        detail="Agent request too large",
    )
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=524288,
        prefixes=("/api/v1/evaluations/",),
        methods=("POST", "PUT"),
        code="evaluation_request_too_large",
        detail="Evaluation request too large",
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(rag_router)
    app.include_router(tools_router)
    app.include_router(agents_router)
    app.include_router(evaluations_router)
    attach_console(app)
    return app
