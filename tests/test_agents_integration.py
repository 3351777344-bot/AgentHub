# ruff: noqa: F811 -- reuse isolated PostgreSQL and real HTTPS MCP fixtures
"""P7 API/worker acceptance with real PostgreSQL, checkpoints and MCP transport."""

import asyncio
import json
import os
import sys
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select, text
from test_identity_integration import (  # noqa: F401
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)
from test_tools_integration import REGISTRATIONS, SERVICES, remote  # noqa: F401

from agenthub.agents.models import AgentRun, AgentStep
from agenthub.agents.worker import Worker
from agenthub.core.config import Settings
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.knowledge import worker as ingestion


def run_async(coro):
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coro)


def completion(*, answer=None, tool=None, arguments=None, tokens=5):
    message = {"role": "assistant", "content": answer}
    if tool:
        message["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments or {})},
            }
        ]
    return httpx2.Response(
        200,
        json={
            "choices": [{"finish_reason": "tool_calls" if tool else "stop", "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": tokens},
        },
    )


def create_run(client, headers, *, tools=(), kb_ids=(), prompt="Calculate the result.", **options):
    config = client.app.state.settings
    if not config.model_name:
        client.app.state.settings = config.model_copy(
            update={
                "model_name": "test-model",
                "model_base_url": "http://localhost:8999",
            }
        )
    agent = client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "P7 acceptance",
            "model_name": client.app.state.settings.model_name,
            "system_prompt": "Follow the user's instructions.",
            "tools": [{"name": name, "version": 1} for name in tools],
            "knowledge_base_ids": list(kb_ids),
            **options,
        },
    )
    assert agent.status_code == 201, agent.text
    run = client.post(
        f"/api/v1/agents/{agent.json()['id']}/runs",
        headers=headers,
        json={"prompt": prompt},
    )
    assert run.status_code == 202, run.text
    return f"/api/v1/agent-runs/{run.json()['id']}"


def advance(client, factory, handler=None):
    async def once():
        transport = httpx2.MockTransport(handler) if handler else None
        async with httpx2.AsyncClient(transport=transport, trust_env=False) as http:
            return await Worker(client.app.state.settings, factory, http).run_once()

    return run_async(once())


def test_checkpoint_replay_pause_and_event_cursor(identity_client, identity_db):
    client = identity_client
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    path = create_run(client, member, tools=("add_integers",))
    assert client.post(path + "/actions/pause", headers=member).json()["status"] == "paused"
    assert not advance(client, identity_db)
    assert client.post(path + "/actions/resume", headers=admin).status_code == 404
    assert client.get(path + "/events", headers=admin).status_code == 404
    assert client.post(path + "/actions/resume", headers=member).json()["status"] == "queued"
    payloads = []

    def model(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            assert payload["tools"][0]["function"]["name"] == "add_integers__v1"
            return completion(tool="add_integers__v1", arguments={"left": 6, "right": 7})
        assert json.loads(payload["messages"][-1]["content"])["result"] == {"total": 13}
        return completion(answer="13")

    assert advance(client, identity_db, model)
    state = client.get(path, headers=member).json()
    assert state["status"] == "completed", state
    assert state["answer"] == "13" and state["completion_tokens"] == 10
    steps = client.get(path + "/steps", headers=member).json()
    assert [s["kind"] for s in steps] == ["model", "tool", "model"]
    assert all(s["status"] == "completed" for s in steps)
    assert steps[1]["tool_call_id"]
    with identity_db() as db:
        row = db.get(AgentRun, UUID(state["id"]))
        assert row.thread_id != row.id
        count = db.scalar(
            text("select count(*) from checkpoints where thread_id=:thread"),
            {"thread": str(row.thread_id)},
        )
        assert count > 4
    sse = client.get(path + "/events", headers={**member, "Last-Event-ID": "2"})
    ids = [int(line[4:]) for line in sse.text.splitlines() if line.startswith("id: ")]
    assert ids == list(range(3, state["event_seq"] + 1))
    assert sse.headers["content-type"].startswith("text/event-stream")
    assert (
        client.get(path + "/events", headers={**member, "Last-Event-ID": "9999"}).status_code == 409
    )


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"max_steps": 1}, "agent_step_budget"),
        ({"max_completion_tokens": 5}, "agent_token_budget"),
        ({"max_seconds": 1}, "agent_time_budget"),
    ],
)
def test_run_budgets(identity_client, identity_db, options, reason):
    client = identity_client
    admin = sign_in(client)
    path = create_run(client, admin, tools=("add_integers",), **options)

    async def model(request):
        if reason == "agent_time_budget":
            await asyncio.sleep(3)
        return completion(tool="add_integers__v1", arguments={"left": 1, "right": 2})

    assert advance(client, identity_db, model)
    state = client.get(path, headers=admin).json()
    assert (state["status"], state["error_code"]) == ("failed", reason)
    assert not state["recoverable"]
    assert client.get("/api/v1/tool-calls", headers=admin).json() == []


def enable_write(client, admin):
    assert client.post(SERVICES, headers=admin, json={"key": "remote"}).status_code == 201
    tools = client.get(SERVICES + "/remote/tools", headers=admin).json()
    descriptor = next(tool for tool in tools if tool["name"] == "remote_write")
    row = client.post(
        REGISTRATIONS,
        headers=admin,
        json={
            "name": "reviewed_write",
            "version": 1,
            "service_key": "remote",
            "remote_name": "remote_write",
            "definition_digest": descriptor["definition_digest"],
            "description": "Record the test write.",
            "effect": "high",
            "required_role": "member",
        },
    )
    assert row.status_code == 201, row.text
    assert not row.json()["agent_enabled"]
    enabled = client.patch(
        REGISTRATIONS + "/reviewed_write/1/agent-execution", headers=admin, json={"enabled": True}
    )
    assert enabled.status_code == 200 and enabled.json()["agent_enabled"], enabled.text
    assert (
        client.post(
            "/api/v1/tools/reviewed_write/1/calls", headers=admin, json={"arguments": {}}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_resume_against_real_mcp(identity_client, identity_db, remote, decision):
    client = identity_client
    launch, marker, _token = remote
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    with launch():
        enable_write(client, admin)
        path = create_run(client, member, tools=("reviewed_write",))
        assert advance(client, identity_db, lambda _: completion(tool="reviewed_write__v1"))
        state = client.get(path, headers=member).json()
        assert state["status"] == "waiting_approval", state
        approval_path = path + "/approvals/" + state["approval_id"]
        approval = client.get(approval_path, headers=member).json()
        assert approval["arguments"] == {} and approval["effect"] == "high"
        assert not marker.exists()
        assert client.get(approval_path, headers=admin).status_code == 404
        assert (
            client.post(
                approval_path,
                headers=member,
                json={
                    "digest": "0" * 64,
                    "decision": decision,
                },
            ).status_code
            == 409
        )
        payload = {"digest": approval["digest"], "decision": decision}
        assert client.post(approval_path, headers=member, json=payload).status_code == 200
        assert client.post(approval_path, headers=member, json=payload).status_code == 409

        def after_restart(request):
            messages = json.loads(request.content)["messages"]
            feedback = json.loads(messages[-1]["content"])
            expected = "completed" if decision == "approve" else "rejected"
            assert feedback["status"] == expected
            return completion(answer=expected)

        assert advance(client, identity_db, after_restart)
        assert client.get(path, headers=member).json()["status"] == "completed"
        assert not advance(client, identity_db, after_restart)
        if decision == "approve":
            assert marker.read_text().splitlines() == ["unexpected write"]
        else:
            assert not marker.exists()


def test_abandoned_claim_and_replayed_tool_result(identity_client, identity_db, monkeypatch):
    from agenthub.agents.ledger import Ledger

    client = identity_client
    admin = sign_in(client)
    path = create_run(client, admin, tools=("add_integers",))
    calls = []

    def model(request):
        calls.append(1)
        if len(calls) == 1:
            return completion(tool="add_integers__v1", arguments={"left": 1, "right": 2})
        assert (
            json.loads(json.loads(request.content)["messages"][-1]["content"])["result"]["total"]
            == 3
        )
        return completion(answer="3")

    async def interrupt_worker():
        save = Ledger.save_result
        loop = asyncio.get_running_loop()

        def interrupted(self, step_id, status, output, error, **usage):
            save(self, step_id, status, output, error, **usage)
            if output == {"total": 3}:
                loop.call_soon_threadsafe(task.cancel)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(model)) as http:
            worker = Worker(client.app.state.settings, identity_db, http)
            with monkeypatch.context() as patch:
                patch.setattr(Ledger, "save_result", interrupted)
                task = asyncio.create_task(worker.run_once())
                with pytest.raises(asyncio.CancelledError):
                    await task

    run_async(interrupt_worker())
    assert client.get(path, headers=admin).json()["status"] == "running"
    assert not advance(client, identity_db, model)
    abandoned = client.get(path, headers=admin).json()
    assert abandoned["error_code"] == "agent_worker_interrupted" and abandoned["recoverable"]
    assert client.post(path + "/actions/resume", headers=admin).status_code == 200
    assert advance(client, identity_db, model)
    assert client.get(path, headers=admin).json()["answer"] == "3"
    assert len(calls) == 2
    assert len(client.get("/api/v1/tool-calls", headers=admin).json()) == 1


def test_inflight_cancel_is_durable(identity_client, identity_db):
    client = identity_client
    admin = sign_in(client)
    path = create_run(client, admin)

    async def scenario():
        entered = asyncio.Event()

        async def model(_request):
            entered.set()
            await asyncio.sleep(30)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(model)) as http:
            worker = Worker(client.app.state.settings, identity_db, http)
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(), 5)
            assert not await worker.run_once()
            response = await asyncio.to_thread(client.post, path + "/actions/cancel", headers=admin)
            assert response.json()["status"] == "cancelled"
            await asyncio.wait_for(task, 5)

    run_async(scenario())
    assert client.get(path, headers=admin).json()["status"] == "cancelled"
    assert client.post(path + "/actions/resume", headers=admin).status_code == 409
    assert client.get(path + "/steps", headers=admin).json()[0]["status"] == "uncertain"


@pytest.mark.parametrize("record_result", [True, False])
def test_write_crash_window(identity_client, identity_db, remote, monkeypatch, record_result):
    from agenthub.agents.ledger import Ledger

    client = identity_client
    launch, marker, _token = remote
    admin = sign_in(client)
    with launch():
        enable_write(client, admin)
        path = create_run(client, admin, tools=("reviewed_write",))
        assert advance(client, identity_db, lambda _: completion(tool="reviewed_write__v1"))
        approval_path = path + "/approvals/" + client.get(path, headers=admin).json()["approval_id"]
        approved = client.get(approval_path, headers=admin).json()
        assert (
            client.post(
                approval_path,
                headers=admin,
                json={
                    "decision": "approve",
                    "digest": approved["digest"],
                },
            ).status_code
            == 200
        )

        async def crash_after_write():
            loop = asyncio.get_running_loop()
            save = Ledger.save_result

            def interrupted(self, step_id, status, output, error, **usage):
                if output != {"total": 0} or record_result:
                    save(self, step_id, status, output, error, **usage)
                if output == {"total": 0}:
                    loop.call_soon_threadsafe(task.cancel)

            def should_not_call_model(_request):
                pytest.fail("Worker should stop before the final model call")

            transport = httpx2.MockTransport(should_not_call_model)
            async with httpx2.AsyncClient(transport=transport) as http:
                worker = Worker(client.app.state.settings, identity_db, http)
                with monkeypatch.context() as patch:
                    patch.setattr(Ledger, "save_result", interrupted)
                    task = asyncio.create_task(worker.run_once())
                    with pytest.raises(asyncio.CancelledError):
                        await task

        run_async(crash_after_write())
        assert marker.read_text().splitlines() == ["unexpected write"]
        assert not advance(client, identity_db)
        recovered = client.get(path, headers=admin).json()
        assert recovered["status"] == "failed"
        resume = client.post(path + "/actions/resume", headers=admin)
        if record_result:
            assert (
                recovered["error_code"] == "agent_worker_interrupted" and recovered["recoverable"]
            )
            assert resume.status_code == 200

            def final_model(request):
                message = json.loads(request.content)["messages"][-1]
                assert json.loads(message["content"])["result"] == {"total": 0}
                return completion(answer="Write completed")

            assert advance(client, identity_db, final_model)
            assert client.get(path, headers=admin).json()["answer"] == "Write completed"
        else:
            assert (
                recovered["error_code"] == "agent_outcome_unknown" and not recovered["recoverable"]
            )
            assert resume.status_code == 409
            step = client.get(path + "/steps", headers=admin).json()[-1]
            assert step["status"] == "uncertain" and step["tool_call_id"]
        assert marker.read_text().splitlines() == ["unexpected write"]


def test_configuration_snapshot_and_runtime_allowlists(identity_client, identity_db):
    from agenthub.knowledge.models import KnowledgeBase

    client = identity_client
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    with identity_db.begin() as db:
        owner = UUID(client.admin_id)
        first, second = [
            KnowledgeBase(
                owner_id=owner,
                name=name,
                dimensions=1024,
                embedding_model="test-model",
                embedding_base_url="https://example.invalid",
                chunk_size=100,
                chunk_overlap=10,
            )
            for name in ("allowed", "private")
        ]
        db.add_all((first, second))
        db.flush()
        allowed, other = str(first.id), str(second.id)
    path = create_run(client, admin, kb_ids=(allowed,), tools=("knowledge_search",))
    state = client.get(path, headers=admin).json()
    agent_path = "/api/v1/agents/" + state["agent_id"]
    config = client.get(agent_path, headers=admin).json()["config"]
    assert client.post("/api/v1/agents", headers=member, json=config).status_code == 404
    replacement = config | {"system_prompt": "New prompt", "tools": [], "knowledge_base_ids": []}
    assert client.put(agent_path, headers=admin, json=replacement).status_code == 200
    responses = iter(
        [
            completion(
                tool="knowledge_search__v1",
                arguments={
                    "knowledge_base_id": other,
                    "question": "private?",
                },
            ),
            completion(tool="add_integers__v1", arguments={"left": 1, "right": 2}),
            completion(answer="Both calls were denied"),
        ]
    )
    observed = []

    def model(request):
        payload = json.loads(request.content)
        assert "Follow the user's instructions." in payload["messages"][0]["content"]
        assert "New prompt" not in payload["messages"][0]["content"]
        assert len(payload["tools"]) == 1
        if payload["messages"][-1]["role"] == "tool":
            observed.append(json.loads(payload["messages"][-1]["content"])["error_code"])
        return next(responses)

    assert advance(client, identity_db, model)
    assert observed == ["agent_knowledge_forbidden", "agent_tool_forbidden"]
    assert client.get(path, headers=admin).json()["answer"] == "Both calls were denied"
    assert client.get("/api/v1/tool-calls", headers=admin).json() == []


@pytest.mark.skipif(
    os.getenv("AGENTHUB_RUN_P7_CLOUD_ACCEPTANCE") != "1",
    reason="Explicit P7 real DeepSeek/Bailian acceptance opt-in required",
)
def test_real_deepseek_retrieves_and_calls_tool(
    identity_client, identity_db, tmp_path, monkeypatch
):
    from test_knowledge_integration import kb_and_headers, upload

    client = identity_client
    cloud = Settings()
    assert cloud.model_name == "deepseek-flash" and cloud.model_api_key.get_secret_value()
    assert cloud.embedding_api_key.get_secret_value()
    config = client.app.state.settings.model_copy(
        update={
            **cloud.model_dump(
                include={
                    "model_base_url",
                    "model_name",
                    "model_api_key",
                    "embedding_base_url",
                    "embedding_model",
                    "embedding_api_key",
                    "embedding_dimensions",
                }
            ),
            "document_storage_path": tmp_path,
        }
    )
    client.app.state.settings = config
    client.app.state.embedding_provider = EmbeddingProvider(
        config, client.app.state.chat_provider.client
    )
    monkeypatch.setattr(client.app.state.knowledge_queue, "notify", lambda: None)
    kb, headers = kb_and_headers(client)
    doc = upload(
        client, kb, headers, b"The fictional Nova Archive closes at 17:45 on Mondays.", "nova.txt"
    )
    job = ingestion.claim(identity_db, config)
    assert job is not None

    async def index_document():
        async with httpx2.AsyncClient(trust_env=False) as http:
            await ingestion.process(identity_db, job, config, EmbeddingProvider(config, http))

    run_async(index_document())
    path = create_run(
        client,
        headers,
        kb_ids=(kb.rsplit("/", 1)[-1],),
        tools=("knowledge_search", "add_integers"),
        prompt="Call knowledge_search to find when the fictional Nova Archive closes on "
        "Mondays. Also call add_integers with 23 and 19. Report both answers and the filename.",
    )
    assert advance(client, identity_db)
    state = client.get(path, headers=headers).json()
    assert state["status"] == "completed", state
    assert all(part in state["answer"] for part in ("17:45", "42", "nova.txt")), state
    with identity_db() as db:
        rows = list(
            db.scalars(
                select(AgentStep)
                .where(AgentStep.run_id == UUID(state["id"]), AgentStep.kind == "tool")
                .order_by(AgentStep.ordinal)
            )
        )
        actual = {
            row.input["tool"]["name"]: row.output for row in rows if row.status == "completed"
        }
        assert actual["add_integers"] == {"total": 42}
        assert actual["knowledge_search"]["hits"][0]["source"]["document_id"] == doc["id"]
    print("P7_REAL_AGENT_USAGE", state["prompt_tokens"], state["completion_tokens"])
