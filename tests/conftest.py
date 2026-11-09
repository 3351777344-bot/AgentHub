"""Deterministic API tests use explicitly identified dependency test doubles."""

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthub.core.config import Settings
from agenthub.core.dependencies import DependencyChecks
from agenthub.main import create_app


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in os.environ:
        if name.startswith("AGENTHUB_"):
            monkeypatch.delenv(name)
    return Settings(_env_file=None, environment="test", dependency_timeout_seconds=0.1)


@pytest.fixture
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # No real services are contacted by this fixture, including during startup.
    monkeypatch.setattr(
        DependencyChecks,
        "check",
        AsyncMock(return_value={"postgresql": "ok", "redis": "ok"}),
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
