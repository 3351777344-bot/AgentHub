import asyncio
import io
import json
import logging

import httpx2 as httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agenthub.core.logging import JsonFormatter, request_id_context


@pytest.mark.parametrize("value", ["client-42", "", "bad id", "x" * 65, "bad\tvalue"])
def test_request_id_is_validated(client: TestClient, value: str) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": value})
    result = response.headers["x-request-id"]
    if value == "client-42":
        assert result == value
    else:
        assert len(result) == 32
        int(result, 16)


def test_unknown_path_is_uniform_and_does_not_echo_path(client: TestClient) -> None:
    response = client.get("/secret-token", headers={"X-Request-ID": "missing-1"})
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "http_404",
        "message": "Not Found",
        "request_id": "missing-1",
        "details": {},
    }
    assert "secret-token" not in response.text


def test_method_error_preserves_allow_header(client: TestClient) -> None:
    response = client.post("/health/live")
    assert response.status_code == 405
    assert "GET" in response.headers["allow"]
    assert response.json()["error"]["code"] == "http_405"


class ExampleInput(BaseModel):
    count: int


def test_validation_does_not_expose_input(app: FastAPI, client: TestClient) -> None:
    @app.post("/_test/input")
    async def validate(body: ExampleInput) -> ExampleInput:
        return body

    response = client.post("/_test/input", json={"count": "private-password"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "private-password" not in response.text


def test_http_error_hides_detail_and_keeps_auth_header(app: FastAPI, client: TestClient) -> None:
    @app.get("/_test/unauthorized")
    async def unauthorized() -> None:
        raise HTTPException(401, "private-password", headers={"WWW-Authenticate": "Bearer"})

    response = client.get("/_test/unauthorized")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "private-password" not in response.text


def test_unhandled_error_keeps_id_and_hides_secrets(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(logging.getLogger("agenthub").handlers[0], "stream", stream)

    @app.get("/_test/failure")
    async def failure() -> None:
        raise RuntimeError("postgresql://user:private-password@host/database")

    response = client.get(
        "/_test/failure?token=private-token", headers={"X-Request-ID": "failure-1"}
    )
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "failure-1"
    assert response.json()["error"]["request_id"] == "failure-1"
    output = stream.getvalue()
    assert "private-password" not in response.text + output
    assert "private-token" not in response.text + output
    events = [json.loads(line) for line in output.splitlines()]
    assert any(
        event["event"] == "request_failed"
        and event["error_type"] == "RuntimeError"
        and event["request_id"] == "failure-1"
        for event in events
    )


def test_concurrent_request_contexts_are_isolated(app: FastAPI, client: TestClient) -> None:
    @app.get("/_test/context")
    async def context(request: Request) -> dict[str, str]:
        await asyncio.sleep(0.01)
        return {"context_id": request_id_context.get(), "state_id": request.state.request_id}

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            responses = await asyncio.gather(
                *[
                    async_client.get("/_test/context", headers={"X-Request-ID": f"id-{i}"})
                    for i in range(8)
                ]
            )
        for i, response in enumerate(responses):
            assert response.json() == {"context_id": f"id-{i}", "state_id": f"id-{i}"}
            assert response.headers["x-request-id"] == f"id-{i}"
        assert request_id_context.get() == "-"

    asyncio.run(run())


def test_formatter_does_not_serialize_exception_or_arbitrary_extra() -> None:
    record = logging.LogRecord("agenthub.test", logging.ERROR, "", 0, "safe_event", (), None)
    record.password = "private-password"
    record.exc_info = (RuntimeError, RuntimeError("private-exception"), None)
    formatted = JsonFormatter().format(record)
    assert "private-password" not in formatted
    assert "private-exception" not in formatted
    assert json.loads(formatted)["event"] == "safe_event"
