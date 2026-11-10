"""Application factory: uvicorn agenthub.main:create_app --factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from agenthub import __version__
from agenthub.api.health import router as health_router
from agenthub.api.identity import router as identity_router
from agenthub.core.config import Settings
from agenthub.core.dependencies import DependencyChecks
from agenthub.core.errors import ErrorResponse, register_error_handlers
from agenthub.core.logging import configure_logging
from agenthub.core.middleware import RequestContextMiddleware
from agenthub.db.session import create_database_engine, create_session_factory

logger = logging.getLogger("agenthub.lifecycle")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        dependencies = DependencyChecks(settings)
        app.state.dependencies = dependencies
        engine = create_database_engine(settings)
        app.state.session_factory = create_session_factory(engine)
        try:
            # Report startup dependency failures, but keep liveness and diagnostics available.
            checks = await dependencies.check()
            logger.info("application_started", extra={"checks": checks})
            yield
        finally:
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
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(identity_router)
    return app
