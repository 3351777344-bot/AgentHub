"""Synchronous psycopg engine, used by FastAPI's synchronous worker-thread routes."""

import math
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agenthub.core.config import Settings


class Base(DeclarativeBase):
    pass


def database_url(settings: Settings) -> URL:
    # URL.create escapes special characters without building/logging a credential string.
    return URL.create(
        "postgresql+psycopg",
        username=settings.database_user,
        password=settings.database_password.get_secret_value(),
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=0,
        pool_timeout=settings.dependency_timeout_seconds,
        hide_parameters=True,
        connect_args={
            "connect_timeout": max(2, math.ceil(settings.dependency_timeout_seconds)),
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    # Services commit before sending success. Closing rolls back any unfinished work,
    # including read-only transactions and failed requests; never commit during teardown.
    with factory() as session:
        yield session
