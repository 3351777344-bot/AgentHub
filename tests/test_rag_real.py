# ruff: noqa: F811 -- reuse isolated database fixtures
"""Opt-in P5 end-to-end baseline using only fixed synthetic public text."""

import asyncio
import os
import time
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import select
from test_identity_integration import (  # noqa: F401
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    sign_in,
)
from test_identity_integration import (
    pytestmark as database_marks,
)
from test_knowledge_integration import kb_and_headers, upload
from test_knowledge_parsing import text_pdf
from test_rag_integration import conversation, events

from agenthub.chat.models import ModelCall
from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.knowledge import worker
from agenthub.knowledge.models import Document, IngestionJob

pytestmark = [
    *database_marks,
    pytest.mark.skipif(
        os.getenv("AGENTHUB_RUN_P5_RAG_ACCEPTANCE") != "1",
        reason="Explicit P5 real-model acceptance opt-in required",
    ),
]

# Human-labeled gold source and answer for each independent public sample.
GOLD = (
    (
        "library.txt",
        "The fictional Cedar Library closes at 18:30 on Fridays.",
        "What time does the fictional Cedar Library close on Fridays?",
        "18:30",
    ),
    (
        "observatory.md",
        "# Fictional Lumen Observatory\nThe observatory dome is made of copper.",
        "What is the fictional Lumen Observatory dome made of?",
        "copper",
    ),
    (
        "museum.pdf",
        "The fictional Orion Museum offers free entry on Tuesdays.",
        "On what day does the fictional Orion Museum offer free entry?",
        "tuesday",
    ),
)
NO_ANSWER = "Which planet has the highest wind speeds in the solar system?"


def test_real_flash_deepseek_and_three_formats(identity_client, identity_db, tmp_path, monkeypatch):
    client = identity_client
    cloud = Settings()
    assert cloud.model_name == "deepseek-flash" and cloud.model_api_key.get_secret_value()
    assert cloud.embedding_model == "qwen3.7-text-embedding-flash"
    assert cloud.embedding_dimensions == 1024 and cloud.embedding_api_key.get_secret_value()
    config = client.app.state.settings.model_copy(
        update={
            **cloud.model_dump(
                include={
                    "embedding_base_url",
                    "embedding_model",
                    "embedding_api_key",
                    "embedding_dimensions",
                    "embedding_timeout_seconds",
                    "embedding_batch_size",
                    "embedding_max_characters",
                    "model_base_url",
                    "model_name",
                    "model_api_key",
                    "model_timeout_seconds",
                }
            ),
            "document_storage_path": tmp_path,
            "model_max_tokens": 256,
            "model_thinking_mode": "disabled",
            "model_include_usage": True,
        }
    )
    client.app.state.settings = config
    http = client.app.state.chat_provider.client
    client.app.state.chat_provider = ChatProvider(config, http)
    client.app.state.embedding_provider = EmbeddingProvider(config, http)
    monkeypatch.setattr(client.app.state.knowledge_queue, "notify", lambda: None)
    kb, headers = kb_and_headers(client)
    kb_id = kb.split("/")[-1]
    conv = conversation(client, headers)
    documents = {}

    async def process(job):
        # Use a separate HTTP client on the worker loop.
        async with httpx2.AsyncClient(trust_env=False) as worker_http:
            await worker.process(identity_db, job, config, EmbeddingProvider(config, worker_http))

    for filename, content, _, _ in GOLD:
        file = text_pdf(content) if filename.endswith(".pdf") else content.encode()
        doc = upload(client, kb, headers, file, filename)
        documents[filename] = doc["id"], file
        job_id = worker.claim(identity_db, config)
        assert job_id is not None
        asyncio.run(process(job_id))
        with identity_db() as db:
            assert db.get(Document, UUID(doc["id"])).status == "ready"

    rankings = []
    answer_rows = []
    for i, (filename, _, question, expected_text) in enumerate(GOLD):
        doc_id, original = documents[filename]
        started = time.monotonic()
        found = client.post(kb + "/search", headers=headers, json={"question": question})
        assert found.status_code == 200, found.text
        ranks = [item["source"]["document_id"] for item in found.json()["hits"]]
        rank = ranks.index(doc_id) + 1 if doc_id in ranks else None
        rankings.append(
            (filename, rank, round(time.monotonic() - started, 3), found.json()["retrieval"])
        )
        stream = i == 1
        started = time.monotonic()
        result = client.post(
            conv + f"/rag?stream={str(stream).lower()}",
            headers=headers,
            json={"question": question, "knowledge_base_id": kb_id},
        )
        wall = round(time.monotonic() - started, 3)
        assert result.status_code == 200, result.text
        if stream:
            frames = events(result)
            assert frames[0][0] == "start" and frames[-1][0] == "done", result.text
            assert any(name == "delta" for name, _ in frames)
            answer = frames[-1][1]
        else:
            answer = result.json()
        assert answer["outcome"] == "answered", answer
        assert expected_text in answer["message"]["content"].lower()
        [citation] = answer["citations"]
        assert citation["available"] and citation["source"]["document_id"] == doc_id
        document_path = kb + "/documents/" + doc_id
        chunks = client.get(document_path + "/chunks", headers=headers).json()
        assert citation["source"]["chunk_id"] == chunks[0]["id"]
        assert citation["source"]["page_number"] == chunks[0]["page_number"] == 1
        assert client.get(document_path + "/source", headers=headers).content == original
        answer_rows.append((filename, wall, answer["retrieval"]))

    started = time.monotonic()
    unknown = client.post(
        conv + "/rag",
        headers=headers,
        json={
            "question": NO_ANSWER,
            "knowledge_base_id": kb_id,
        },
    )
    assert unknown.status_code == 200, unknown.text
    assert (
        unknown.json()["outcome"] == "insufficient_evidence" and unknown.json()["citations"] == []
    )
    print("P5_NO_ANSWER", round(time.monotonic() - started, 3), unknown.json()["retrieval"])
    add_user(client, headers)
    member = sign_in(client, "member")
    member_conv = conversation(client, member)
    forbidden = client.post(
        member_conv + "/rag",
        headers=member,
        json={
            "question": GOLD[0][2],
            "knowledge_base_id": kb_id,
        },
    )
    assert forbidden.status_code == 404
    assert client.get(member_conv + "/messages", headers=member).json() == []
    with identity_db() as db:
        print("P5_INGEST_EMBEDDING_TOKENS", list(db.scalars(select(IngestionJob.total_tokens))))
        calls = list(db.scalars(select(ModelCall).order_by(ModelCall.id)))
        assert 3 <= len(calls) <= 4
        assert all(call.prompt_tokens and call.completion_tokens for call in calls)
        print(
            "P5_GENERATION", [(c.prompt_tokens, c.completion_tokens, c.duration_ms) for c in calls]
        )
    print("P5_RETRIEVAL", rankings)
    print("P5_ANSWERS", answer_rows)
    assert [row[1] for row in rankings] == [1, 1, 1]
