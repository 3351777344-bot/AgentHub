from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenthub.api.health import get_readiness_checker


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_readiness_success(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgresql": "ok", "redis": "ok"},
    }


@pytest.mark.parametrize(
    "checks",
    [
        {"postgresql": "error", "redis": "ok"},
        {"postgresql": "ok", "redis": "error"},
        {"postgresql": "error", "redis": "error"},
        {},
    ],
)
def test_readiness_failure_keeps_liveness_available(
    app: FastAPI, client: TestClient, checks: dict[str, str]
) -> None:
    checker = AsyncMock()
    checker.check.return_value = checks
    app.dependency_overrides[get_readiness_checker] = lambda: checker
    response = client.get("/health/ready", headers={"X-Request-ID": "probe-42"})
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "not_ready",
        "message": "Required dependencies are unavailable",
        "request_id": "probe-42",
        "details": checks,
    }
    assert client.get("/health/live").status_code == 200


def test_openapi_matches_public_routes(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert {path for path in schema["paths"] if path.startswith("/health/")} == {
        "/health/live",
        "/health/ready",
    }
    assert schema["paths"]["/health/ready"]["get"]["responses"]["503"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
