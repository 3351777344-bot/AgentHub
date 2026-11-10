# ruff: noqa: F811 -- pytest fixtures imported for reuse
"""Real PostgreSQL chat; model HTTP transport is an explicit test double."""

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx2 as httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_identity_integration import (  # noqa: F401 -- shared isolated DB fixtures
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)

from agenthub.api.chat import execute
from agenthub.chat import service
from agenthub.chat.models import Conversation, Message, ModelCall
from agenthub.chat.provider import ChatProvider
from agenthub.core.errors import AppError
from agenthub.main import create_app


def test_chat_history_survives_real_api_process_restart(chat_client, p2_settings, tmp_path):
    headers = sign_in(chat_client)
    cid = conversation(chat_client, headers)
    path = f"/api/v1/conversations/{cid}/messages"
    generated = chat_client.post(path, headers=headers, json={"content": "Public restart test"})
    assert generated.status_code == 200
    expected = chat_client.get(path, headers=headers).json()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENTHUB_")}
    environment.update(
        AGENTHUB_ENVIRONMENT="test",
        AGENTHUB_DATABASE_HOST=p2_settings.database_host,
        AGENTHUB_DATABASE_PORT=str(p2_settings.database_port),
        AGENTHUB_DATABASE_NAME=p2_settings.database_name,
        AGENTHUB_DATABASE_USER=p2_settings.database_user,
        AGENTHUB_DATABASE_PASSWORD=p2_settings.database_password.get_secret_value(),
        AGENTHUB_REDIS_URL="redis://127.0.0.1:1/0",
        AGENTHUB_DEPENDENCY_TIMEOUT_SECONDS="0.1",
    )
    # Each server runs outside the repo with no .env/model credentials. It only
    # reads committed chat history; generation above uses the explicit test double.
    for attempt in range(2):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with (tmp_path / f"chat-restart-{attempt}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "agenthub.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=tmp_path,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            if http.get("/health/live", timeout=1).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        assert process.poll() is None and time.monotonic() < deadline
                        time.sleep(0.05)
                    result = http.get(path, headers=headers, timeout=3)
                    assert result.status_code == 200 and result.json() == expected
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.fixture
def chat_client(identity_client):
    client = identity_client
    settings = client.app.state.settings.model_copy(
        update={
            "model_base_url": "https://test-model.invalid/v1",
            "model_name": "test-model",
        }
    )
    client.app.state.settings = settings
    original = client.app.state.chat_provider.client
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "test answer"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )
    )
    mock_client = httpx.AsyncClient(transport=transport)
    client.app.state.chat_provider = ChatProvider(settings, mock_client)
    yield client
    asyncio.run(mock_client.aclose())
    client.app.state.chat_provider.client = original


def conversation(client, headers):
    response = client.post("/api/v1/conversations", headers=headers, json={"title": "test"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_chat_persistence_usage_pagination_and_ownership(chat_client, identity_db, p2_settings):
    client = chat_client
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    cid = conversation(client, member)
    path = f"/api/v1/conversations/{cid}/messages"
    reply = client.post(path, headers=member, json={"content": "hello"})
    assert reply.status_code == 200 and reply.json()["status"] == "completed", reply.text
    assert client.get(path, headers=admin).status_code == 404
    assert client.post(path, headers=admin, json={"content": "steal"}).status_code == 404
    assert client.get("/api/v1/conversations", headers=admin).json() == []
    assert len(client.get(path + "?limit=1&offset=1", headers=member).json()) == 1
    assert client.get(path + "?limit=101", headers=member).status_code == 422
    with identity_db() as db:
        call = db.scalar(select(ModelCall))
        assert call.model == "test-model" and call.prompt_tokens == 4
        assert call.completion_tokens == 2 and call.duration_ms >= 0
    with TestClient(create_app(p2_settings)) as restarted:
        history = restarted.get(path, headers=member).json()
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert history[-1]["content"] == "test answer"


def test_sse_done_and_error_saved(chat_client, identity_db):
    client = chat_client
    headers = sign_in(client)
    cid = conversation(client, headers)
    path = f"/api/v1/conversations/{cid}/messages"
    transport = client.app.state.chat_provider.client
    transport._transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )
    )
    response = client.post(path + "?stream=true", headers=headers, json={"content": "hello"})
    assert response.status_code == 200
    assert "event: start" in response.text and "event: delta" in response.text
    assert "event: done" in response.text
    transport._transport = httpx.MockTransport(lambda r: httpx.Response(429, text="private"))
    response = client.post(path + "?stream=true", headers=headers, json={"content": "again"})
    assert "event: error" in response.text and "event: done" not in response.text
    history = client.get(path, headers=headers).json()
    assert history[-1]["status"] == "failed"
    assert history[-1]["error_code"] == "model_rate_limited"
    assert "private" not in response.text


def test_concurrent_turn_lease_recovery_and_failed_context(chat_client, identity_db):
    client = chat_client
    headers = sign_in(client)
    cid = UUID(conversation(client, headers))
    with identity_db() as db:
        owner = db.get(Conversation, cid).owner_id
    settings = client.app.state.settings

    def prepare():
        try:
            return service.prepare(identity_db, cid, owner, "hello", settings, "test")
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prepare(), range(2)))
    assert results.count("conversation_busy") == 1
    with identity_db.begin() as db:
        db.execute(update(Conversation).values(busy_until=datetime.now(UTC) - timedelta(seconds=1)))
    mid, prompt = service.prepare(identity_db, cid, owner, "retry", settings, "test")
    assert prompt == [{"role": "user", "content": "retry"}]
    with identity_db() as db:
        old = db.scalar(select(Message).where(Message.position == 2))
        assert old.status == "failed" and old.error_code == "generation_interrupted"
    # A late finalizer cannot clear the replacement generation's lease.
    old_id = next(r[0] for r in results if isinstance(r, tuple))
    service.finish(identity_db, cid, old_id, "late", "completed", None, 1, None, None)
    with identity_db() as db:
        assert db.get(Conversation, cid).busy_until is not None


def test_cancelled_generator_saves_partial_output(chat_client, identity_db):
    client = chat_client
    headers = sign_in(client)
    cid = UUID(conversation(client, headers))
    with identity_db() as db:
        owner = db.get(Conversation, cid).owner_id
    mid, prompt = service.prepare(
        identity_db, cid, owner, "hello", client.app.state.settings, "cancel"
    )

    class SlowProvider:
        async def generate(self, *args, **kwargs):
            from agenthub.chat.provider import Chunk

            yield Chunk(text="partial")
            await asyncio.sleep(10)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=client.app.state.settings,
                session_factory=identity_db,
                chat_provider=SlowProvider(),
            )
        ),
        state=SimpleNamespace(request_id="cancel"),
    )

    async def cancel():
        generator = execute(request, cid, mid, prompt, True)
        await anext(generator)  # start
        await anext(generator)  # partial delta
        task = asyncio.create_task(anext(generator))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    with identity_db() as db:
        message = db.get(Message, mid)
        assert message.status == "cancelled" and message.content == "partial"
        assert db.get(Conversation, cid).busy_until is None


def test_total_timeout_and_unconfigured_do_not_leave_busy_conversation(chat_client, identity_db):
    client = chat_client
    headers = sign_in(client)
    cid = conversation(client, headers)
    path = f"/api/v1/conversations/{cid}/messages"

    class SlowProvider:
        def check_configured(self):
            pass

        async def generate(self, *args, **kwargs):
            await asyncio.sleep(5)
            yield  # Never reached before total timeout.

    client.app.state.settings = client.app.state.settings.model_copy(
        update={"model_timeout_seconds": 1}
    )
    original = client.app.state.chat_provider
    client.app.state.chat_provider = SlowProvider()
    response = client.post(path, headers=headers, json={"content": "hello"})
    assert response.status_code == 504
    history = client.get(path, headers=headers).json()
    assert history[-1]["status"] == "failed" and history[-1]["error_code"] == "model_timeout"
    client.app.state.chat_provider = ChatProvider(
        original.settings.model_copy(update={"model_name": ""}), original.client
    )
    response = client.post(path, headers=headers, json={"content": "hello"})
    assert response.status_code == 503
    assert len(client.get(path, headers=headers).json()) == 2


def test_real_tcp_stream_disconnect_persists_cancellation(chat_client, identity_db, p2_settings):
    headers = sign_in(chat_client)
    cid = conversation(chat_client, headers)
    settings = p2_settings.model_copy(
        update={
            "model_base_url": "https://test.invalid/v1",
            "model_name": "test-model",
        }
    )
    app = create_app(settings)

    class SlowProvider:
        def check_configured(self):
            pass

        async def generate(self, *args, **kwargs):
            from agenthub.chat.provider import Chunk

            yield Chunk(text="partial")
            await asyncio.sleep(30)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                time.sleep(0.02)
            app.state.chat_provider = SlowProvider()
            with httpx.Client(trust_env=False) as http:
                with http.stream(
                    "POST",
                    f"http://127.0.0.1:{sock.getsockname()[1]}"
                    f"/api/v1/conversations/{cid}/messages?stream=true",
                    headers=headers,
                    json={"content": "disconnect"},
                ) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if "partial" in line:
                            break
                deadline = time.monotonic() + 5
                while True:
                    with identity_db() as db:
                        message = db.scalar(
                            select(Message).where(
                                Message.conversation_id == UUID(cid), Message.role == "assistant"
                            )
                        )
                        if message.status == "cancelled":
                            assert message.content == "partial"
                            break
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
