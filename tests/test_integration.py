"""Opt-in real service tests. Never silently substitute fake services."""

import os

import pytest
from fastapi.testclient import TestClient

from agenthub.main import create_app


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("AGENTHUB_RUN_INTEGRATION") != "1",
    reason="Set AGENTHUB_RUN_INTEGRATION=1 and configure real PostgreSQL + pgvector and Redis",
)
def test_real_dependency_readiness() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200, response.text
        assert response.json()["checks"] == {"postgresql": "ok", "redis": "ok"}
