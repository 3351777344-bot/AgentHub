# ruff: noqa: F811 -- reuse the isolated PostgreSQL fixtures
"""P8 API and workers with real PostgreSQL and explicit model transport selection."""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select
from test_agents_integration import completion, enable_write, run_async
from test_identity_integration import (  # noqa: F401
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)
from test_knowledge_integration import kb_and_headers, upload
from test_tools_integration import remote  # noqa: F401

from agenthub.agents.models import AgentRun
from agenthub.agents.worker import Worker as AgentWorker
from agenthub.core.config import Settings
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.evaluations.models import EvaluationRun
from agenthub.evaluations.worker import Worker
from agenthub.knowledge import worker as ingestion

ROOT = "/api/v1/evaluations"
ADD = {"name": "add_integers", "version": 1}
FIXTURE = Path(__file__).parent / "fixtures" / "p8_regression.json"


def configure(client, tmp_path, monkeypatch, *, cloud=None):
    settings = client.app.state.settings
    if cloud is None:
        updates = {
            "model_base_url": "https://p8-model.invalid",
            "model_name": "p8-test-model",
            "embedding_base_url": "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "embedding_model": "p8-test-embedding",
            "embedding_api_key": "local-test-only",
        }
    else:
        updates = cloud.model_dump(
            include={
                "model_base_url",
                "model_name",
                "model_api_key",
                "embedding_base_url",
                "embedding_model",
                "embedding_api_key",
                "embedding_dimensions",
            }
        )
    settings = settings.model_validate(
        settings.model_dump()
        | updates
        | {
            "document_storage_path": tmp_path,
            "model_max_tokens": 128,
            "model_thinking_mode": "disabled",
            "rag_min_similarity": 0.4,
        }
    )
    client.app.state.settings = settings
    client.app.state.embedding_provider = EmbeddingProvider(
        settings, client.app.state.chat_provider.client
    )
    monkeypatch.setattr(client.app.state.knowledge_queue, "notify", lambda: None)
    return settings


def deterministic(request):
    payload = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        data = []
        for index, text in enumerate(payload["input"]):
            text = text.casefold()
            axis = 0 if "nova archive" in text else 1 if "atlas conservatory" in text else 2
            vector = [0.0] * 1024
            vector[axis] = 1.0
            data.append({"index": index, "embedding": vector})
        return httpx2.Response(
            200, json={"model": payload["model"], "data": data, "usage": {"total_tokens": 3}}
        )
    messages = payload["messages"]
    user = messages[1]["content"]
    if "provided sources" in messages[0]["content"]:
        text = "17:45 [S1]" if "Nova Archive" in json.loads(user)["question"] else "copper [S1]"
        return completion(answer=text)
    if messages[-1]["role"] == "tool":
        reply = json.loads(messages[-1]["content"])
        if "private_export" in user:
            assert reply["error_code"] == "agent_tool_forbidden"
            return completion(answer="Access denied.")
        assert reply["result"] == {"total": 42}
        return completion(answer="42")
    if "private_export" in user:
        return completion(tool="private_export__v1")
    if payload.get("tools"):
        return completion(tool="add_integers__v1", arguments={"left": 23, "right": 19})
    return completion(answer="42")


def client_transport(handler):
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler) if handler else None, trust_env=False
    )


def advance(client, factory, handler=deterministic):
    async def once():
        async with client_transport(handler) as http:
            return await Worker(client.app.state.settings, factory, http).run_once()

    return run_async(once())


def prepare_dataset(client, factory, *, handler=deterministic, controlled=True):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    kb_path, headers = kb_and_headers(client)
    settings = client.app.state.settings
    kb_id = kb_path.split("/")[-1]
    chunks = {}
    for document in data.pop("documents"):
        name = document["filename"]
        doc = upload(client, kb_path, headers, document["content"].encode(), name)
        job_id = ingestion.claim(factory, settings)
        assert job_id is not None

        async def process(job_id):
            async with client_transport(handler) as http:
                await ingestion.process(
                    factory, job_id, settings, EmbeddingProvider(settings, http)
                )

        run_async(process(job_id))
        result = client.get(kb_path + "/documents/" + doc["id"] + "/chunks", headers=headers)
        assert result.status_code == 200, result.text
        [chunk] = result.json()
        chunks[name] = chunk["id"]
    cases = []
    for case in data["cases"]:
        if not controlled and "controlled-provider" in case["tags"]:
            continue
        if case["kind"] == "rag":
            case["knowledge_base_id"] = kb_id
            case["gold_chunk_ids"] = [chunks[name] for name in case.pop("gold_filenames")]
        cases.append(case)
    data["knowledge_base_ids"] = [kb_id]
    data["cases"] = cases
    return data, headers, kb_path


def create_dataset(client, headers, data):
    result = client.post(ROOT + "/datasets", headers=headers, json=data)
    assert result.status_code == 201, result.text
    return result.json()


def agent(client, headers, *, tools=(ADD,), prompt="Use the available tools when asked."):
    result = client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "P8 Agent",
            "model_name": client.app.state.settings.model_name,
            "system_prompt": prompt,
            "tools": list(tools),
            "max_completion_tokens": 256,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def start(client, headers, dataset, name="baseline", **options):
    result = client.post(
        ROOT + "/runs",
        headers=headers,
        json={"dataset_id": dataset["id"], "configuration_version": name, **options},
    )
    assert result.status_code == 202, result.text
    return f"{ROOT}/runs/{result.json()['id']}", result.json()


def agent_only(client, headers, *, count=2):
    return create_dataset(
        client,
        headers,
        {
            "name": "agent",
            "version": "v1",
            "reviewed": True,
            "cases": [
                {
                    "kind": "agent",
                    "key": f"case-{n}",
                    "question": "Calculate 23 plus 19.",
                    "expected_tools": [],
                    "answer_contains": ["42"],
                }
                for n in range(count)
            ],
        },
    )


def test_mixed_comparison_and_owner_permissions(
    identity_client, identity_db, tmp_path, monkeypatch
):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    data, headers, _ = prepare_dataset(client, identity_db)
    dataset = create_dataset(client, headers, data)
    assert client.post(ROOT + "/datasets", headers=headers, json=data).status_code == 409
    assert (
        client.post(
            ROOT + "/datasets", headers=headers, json=data | {"reviewed": False}
        ).status_code
        == 422
    )
    bad = data["cases"][0] | {"gold_chunk_ids": [data["knowledge_base_ids"][0]]}
    assert (
        client.post(
            ROOT + "/datasets", headers=headers, json=data | {"version": "bad", "cases": [bad]}
        ).json()["error"]["code"]
        == "evaluation_reference_invalid"
    )

    profile = agent(client, headers)
    path, baseline = start(client, headers, dataset, agent_id=profile["id"], top_k=2)
    candidate, queued = start(
        client, headers, dataset, "candidate", agent_id=profile["id"], top_k=1
    )
    assert baseline["dataset_digest"] == queued["dataset_digest"]
    assert baseline["config_digest"] != queued["config_digest"]
    assert client.get(path + "/report", headers=headers).status_code == 409
    assert (
        client.put(
            f"/api/v1/agents/{profile['id']}",
            headers=headers,
            json=profile["config"] | {"system_prompt": "Changed after queuing"},
        ).status_code
        == 200
    )

    assert advance(client, identity_db)

    def regress(request):
        payload = json.loads(request.content)
        if (
            "messages" in payload
            and "provided sources" in payload["messages"][0]["content"]
            and "Nova Archive" in payload["messages"][1]["content"]
        ):
            return completion(answer="17:45 [S99]")
        return deterministic(request)

    assert advance(client, identity_db, regress)
    report = client.get(path + "/report", headers=headers).json()
    assert report["task_success_rate"] == report["recall_at_k"] == report["citation_precision"] == 1
    assert report["agent_cases"] == 2 and report["tool_selection_rate"] == 1
    assert report["completion_tokens"] == {"total": 30, "reported_cases": 4}
    assert report["completion_tokens_reserved"] == 30
    assert report["embedding_tokens"] == {"total": 9, "reported_cases": 3}
    outcomes = client.get(path + "/results", headers=headers).json()
    assert [item["status"] for item in outcomes] == ["passed"] * 5
    assert outcomes[-1]["observation"]["steps"][1]["requested_alias"] == "private_export__v1"
    assert outcomes[-1]["observation"]["steps"][1]["status"] == "rejected"
    with identity_db() as db:
        assert {row.config["system_prompt"] for row in db.scalars(select(AgentRun))} == {
            profile["config"]["system_prompt"]
        }
    comparison = client.get(
        ROOT + "/compare",
        headers=headers,
        params={"baseline_id": baseline["id"], "candidate_id": queued["id"]},
    ).json()
    assert comparison["regressions"] == ["archive-hours"] and comparison["improvements"] == []
    assert comparison["cases"][0]["candidate_error"] == "rag_invalid_citations"
    assert comparison["cases"][0]["recall_delta"] == 0
    assert comparison["candidate_report"]["task_success_rate"] == 0.8
    assert client.get(candidate + "/results", headers=headers).json()[0]["failure_stage"] == "model"
    review = client.put(
        path + "/results/archive-hours/review",
        headers=headers,
        json={"passed": False, "note": "Manual review example"},
    )
    assert review.status_code == 200 and review.json()["status"] == "passed"
    report = client.get(path + "/report", headers=headers).json()
    assert report["human_reviewed"] == 1 and report["human_passed"] == 0
    assert report["passed"] == 5

    add_user(client, headers)
    member = sign_in(client, "member")
    for private in (ROOT + "/datasets/" + dataset["id"], path, path + "/report", path + "/results"):
        assert client.get(private, headers=member).status_code == 404
    assert client.get(ROOT + "/runs", headers=member).json() == []
    assert client.get(ROOT + "/datasets", headers=member).json() == []
    assert client.post(path + "/cancel", headers=member).status_code == 404


def test_threshold_regression_is_attributed_to_retrieval(
    identity_client, identity_db, tmp_path, monkeypatch
):
    client = identity_client
    configure(client, tmp_path, monkeypatch)

    def lower_similarity(request):
        response = deterministic(request)
        if request.url.path.endswith("/embeddings") and "What time" in request.content.decode():
            data = response.json()
            data["data"][0]["embedding"][:2] = [0.8, 0.6]
            return httpx2.Response(200, json=data)
        return response

    data, headers, _ = prepare_dataset(client, identity_db)
    dataset = create_dataset(client, headers, data | {"cases": data["cases"][:1]})
    baseline_path, baseline = start(client, headers, dataset, top_k=1, min_similarity=0.5)
    candidate_path, candidate = start(
        client, headers, dataset, "higher-threshold", top_k=1, min_similarity=0.9
    )
    assert advance(client, identity_db, lower_similarity)
    assert advance(client, identity_db, lower_similarity)
    assert client.get(baseline_path + "/report", headers=headers).json()["recall_at_k"] == 1
    [row] = client.get(candidate_path + "/results", headers=headers).json()
    assert row["failure_stage"] == "retrieval"
    assert row["score"]["recall_at_k"] == 0
    assert row["observation"]["outcome"] == "insufficient_evidence"
    assert row["observation"]["model_ms"] is None
    comparison = client.get(
        ROOT + "/compare",
        headers=headers,
        params={"baseline_id": baseline["id"], "candidate_id": candidate["id"]},
    ).json()
    assert comparison["regressions"] == ["archive-hours"]
    assert comparison["cases"][0]["recall_delta"] == -1


def test_corpus_drift_marks_batch_failed(identity_client, identity_db, tmp_path, monkeypatch):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    data, headers, kb = prepare_dataset(client, identity_db)
    dataset = create_dataset(client, headers, data | {"cases": data["cases"][:3]})
    path, _ = start(client, headers, dataset)
    [doc, *_] = client.get(kb + "/documents", headers=headers).json()
    assert client.delete(kb + "/documents/" + doc["id"], headers=headers).status_code == 204
    assert advance(
        client, identity_db, lambda _: pytest.fail("Corpus drift must stop upstream calls")
    )
    state = client.get(path, headers=headers).json()
    assert state["status"] == "failed" and state["error_code"] == "evaluation_corpus_changed"
    assert client.get(path + "/report", headers=headers).json()["skipped"] == 3
    assert (
        client.post(
            ROOT + "/runs",
            headers=headers,
            json={"dataset_id": dataset["id"], "configuration_version": "stale"},
        ).status_code
        == 409
    )


def test_batch_budget_and_case_timeout(identity_client, identity_db, tmp_path, monkeypatch):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    headers = sign_in(client)
    dataset = agent_only(client, headers)
    profile = agent(client, headers, tools=())
    path, _ = start(
        client,
        headers,
        dataset,
        agent_id=profile["id"],
        max_completion_tokens=256,
        case_timeout_seconds=1,
    )

    async def slow(_):
        await asyncio.sleep(30)

    assert advance(client, identity_db, slow)
    assert client.get(path, headers=headers).json()["error_code"] == "evaluation_token_budget"
    [timed, skipped] = client.get(path + "/results", headers=headers).json()
    assert (timed["status"], timed["error_code"]) == ("failed", "evaluation_case_timeout")
    assert skipped["status"] == "skipped"
    assert client.get(path + "/report", headers=headers).json()["completion_tokens_reserved"] == 256
    with identity_db() as db:
        assert db.get(AgentRun, UUID(timed["agent_run_id"])).status == "cancelled"


def test_run_timeout_and_queued_cancel(identity_client, identity_db, tmp_path, monkeypatch):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    headers = sign_in(client)
    dataset = agent_only(client, headers)
    profile = agent(client, headers, tools=())
    queued, _ = start(client, headers, dataset, agent_id=profile["id"])
    assert client.post(queued + "/cancel", headers=headers).json()["status"] == "cancelled"
    assert client.post(queued + "/cancel", headers=headers).json()["status"] == "cancelled"
    path, _ = start(client, headers, dataset, agent_id=profile["id"], run_timeout_seconds=1)

    async def slow(_):
        await asyncio.sleep(30)

    assert advance(client, identity_db, slow)
    assert client.get(path, headers=headers).json()["error_code"] == "evaluation_run_timeout"
    report = client.get(path + "/report", headers=headers).json()
    assert report["failed"] == report["skipped"] == 1 and report["passed"] == 0
    assert report["task_success_rate"] == 0


@pytest.mark.parametrize("crash", [False, True])
def test_only_one_worker_and_no_replay_after_interrupt(
    identity_client, identity_db, tmp_path, monkeypatch, crash
):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    headers = sign_in(client)
    dataset = agent_only(client, headers)
    profile = agent(client, headers, tools=())
    path, _ = start(client, headers, dataset, agent_id=profile["id"])

    async def scenario():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def model(_):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        settings = client.app.state.settings
        async with client_transport(model) as http:
            worker = Worker(settings, identity_db, http)
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(), 5)
            assert not await worker.run_once()
            assert not await AgentWorker(settings, identity_db, http).run_once()
            [first, _] = client.get(path + "/results", headers=headers).json()
            child = "/api/v1/agent-runs/" + first["agent_run_id"]
            assert client.post(child + "/actions/cancel", headers=headers).status_code == 409
            if crash:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not await worker.run_once()
            else:
                result = client.post(path + "/cancel", headers=headers)
                assert result.json()["status"] == "cancelled"
                assert await asyncio.wait_for(task, 5)
            assert cancelled.is_set()

    run_async(scenario())
    state = client.get(path, headers=headers).json()
    expected = ("failed", "evaluation_worker_interrupted") if crash else ("cancelled", None)
    assert (state["status"], state["error_code"]) == expected
    results = client.get(path + "/results", headers=headers).json()
    assert [row["status"] for row in results] == [expected[0] if crash else "skipped", "skipped"]
    with identity_db() as db:
        assert db.get(AgentRun, UUID(results[0]["agent_run_id"])).status == "cancelled"
        assert db.get(EvaluationRun, UUID(state["id"])).reserved_tokens == 256


def test_evaluation_disallows_write_tools(
    identity_client, identity_db, tmp_path, monkeypatch, remote
):
    client = identity_client
    configure(client, tmp_path, monkeypatch)
    headers = sign_in(client)
    dataset = agent_only(client, headers)
    launch, marker, _ = remote
    with launch():
        enable_write(client, headers)
        profile = agent(client, headers, tools=({"name": "reviewed_write", "version": 1},))
        response = client.post(
            ROOT + "/runs",
            headers=headers,
            json={
                "dataset_id": dataset["id"],
                "configuration_version": "unsafe",
                "agent_id": profile["id"],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "evaluation_tool_effect"
        assert not marker.exists()


@pytest.mark.skipif(
    os.getenv("AGENTHUB_RUN_P8_CLOUD_ACCEPTANCE") != "1",
    reason="Explicit P8 real DeepSeek/Bailian acceptance opt-in required",
)
def test_real_cloud_compares_public_regression(identity_client, identity_db, tmp_path, monkeypatch):
    client = identity_client
    cloud = Settings()
    assert cloud.model_api_key.get_secret_value() and cloud.embedding_api_key.get_secret_value()
    configure(client, tmp_path, monkeypatch, cloud=cloud)
    data, headers, _ = prepare_dataset(client, identity_db, handler=None, controlled=False)
    dataset = create_dataset(client, headers, data)
    baseline_agent = agent(client, headers)
    candidate_agent = agent(client, headers, tools=())
    first, baseline = start(client, headers, dataset, agent_id=baseline_agent["id"], top_k=2)
    second, candidate = start(
        client,
        headers,
        dataset,
        "without-add-tool",
        agent_id=candidate_agent["id"],
        top_k=2,
    )
    assert advance(client, identity_db, handler=None)
    assert advance(client, identity_db, handler=None)
    left = client.get(first + "/results", headers=headers).json()
    right = client.get(second + "/results", headers=headers).json()
    comparison = client.get(
        ROOT + "/compare",
        headers=headers,
        params={
            "baseline_id": baseline["id"],
            "candidate_id": candidate["id"],
        },
    ).json()
    print("P8_CLOUD_BASELINE", comparison["baseline_report"])
    print("P8_CLOUD_CANDIDATE", comparison["candidate_report"])
    print(
        "P8_CLOUD_CASES", [(row["key"], row["status"], row["error_code"]) for row in left + right]
    )
    assert all(row["status"] == "passed" for row in left), left
    assert comparison["regressions"] == ["sum"], right
    assert right[-1]["failure_stage"] == "tool"
