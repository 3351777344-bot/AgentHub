# ruff: noqa: F811 -- isolated shared PostgreSQL fixtures
"""Real PostgreSQL/vector/files/parser/API; only model transport and wake hint are doubles."""

import asyncio
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_identity_integration import (  # noqa: F401
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)
from test_knowledge_parsing import text_pdf

from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.identity.models import User
from agenthub.knowledge import worker
from agenthub.knowledge.models import Document, DocumentChunk, IngestionJob
from agenthub.knowledge.queue import WakeupQueue
from agenthub.knowledge.storage import file_path


@pytest.fixture
def knowledge_client(identity_client, tmp_path, monkeypatch):
    client = identity_client
    settings = client.app.state.settings.model_copy(
        update={
            "document_storage_path": tmp_path,
            "embedding_base_url": "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "embedding_api_key": SecretStr("test-double-key"),
            "embedding_model": "qwen3.7-text-embedding-flash",
            "embedding_batch_size": 2,
        }
    )
    client.app.state.settings = settings
    monkeypatch.setattr(client.app.state.knowledge_queue, "notify", lambda: None)

    def success(request):
        import json

        data = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "model": settings.embedding_model,
                "data": [
                    {"index": i, "embedding": [1.0, float(i + 1)] + [0.0] * 1022}
                    for i, _ in enumerate(data["input"])
                ],
                "usage": {"total_tokens": 3},
            },
        )

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(success))
    client.app.state.embedding_provider = EmbeddingProvider(settings, http)
    yield client
    asyncio.run(http.aclose())


def kb_and_headers(client):
    headers = sign_in(client)
    result = client.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={"name": "Test knowledge", "chunk_size": 100, "chunk_overlap": 10},
    )
    assert result.status_code == 201, result.text
    return f"/api/v1/knowledge-bases/{result.json()['id']}", headers


def upload(client, path, headers, content=b"Public knowledge.", filename="test.txt"):
    result = client.post(
        path + "/documents",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert result.status_code == 202, result.text
    return result.json()


def ingest(client, factory):
    settings = client.app.state.settings
    job_id = worker.claim(factory, settings)
    assert job_id is not None
    asyncio.run(worker.process(factory, job_id, settings, client.app.state.embedding_provider))
    return job_id


@pytest.mark.parametrize(
    "filename,content",
    [
        ("public.txt", "公开知识库入库测试。".encode()),
        ("public.md", b"# Public heading\n\nAgentHub knowledge."),
        ("public.pdf", text_pdf()),
    ],
)
def test_real_files_vectors_sources_and_idempotence(
    knowledge_client, identity_db, filename, content
):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers, content, filename)
    dp = path + "/documents/" + doc["id"]
    job_id = ingest(client, identity_db)
    assert client.get(dp, headers=headers).json()["status"] == "ready"
    assert client.get(dp + "/source", headers=headers).content == content
    chunks = client.get(dp + "/chunks", headers=headers).json()
    assert len(chunks) == 1 and chunks[0]["page_number"] == 1
    jobs = client.get(dp + "/jobs", headers=headers).json()
    assert jobs[0]["status"] == "completed" and jobs[0]["total_tokens"] == 3
    with identity_db() as db:
        chunk = db.scalar(select(DocumentChunk))
        assert len(chunk.embedding) == 1024
        assert db.scalar(text("select vector_dims(embedding) from document_chunks")) == 1024
        distance = db.scalar(select(DocumentChunk.embedding.cosine_distance(chunk.embedding)))
        assert abs(distance) < 1e-5
    asyncio.run(
        worker.process(
            identity_db, job_id, client.app.state.settings, client.app.state.embedding_provider
        )
    )
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 1
    assert worker.claim(identity_db, client.app.state.settings) is None
    assert client.post(dp + "/retry", headers=headers).status_code == 409


def test_private_permissions_pagination_duplicate_and_filename(knowledge_client):
    client = knowledge_client
    path, admin = kb_and_headers(client)
    doc = upload(client, path, admin)
    dp = path + "/documents/" + doc["id"]
    add_user(client, admin)
    member = sign_in(client, "member")
    assert client.get("/api/v1/knowledge-bases", headers=member).json() == []
    for suffix in [
        "",
        "/documents",
        "/documents/" + doc["id"],
        "/documents/" + doc["id"] + "/source",
        "/documents/" + doc["id"] + "/jobs",
        "/documents/" + doc["id"] + "/chunks",
    ]:
        assert client.get(path + suffix, headers=member).status_code == 404
    assert client.delete(dp, headers=member).status_code == 404
    assert client.delete(path, headers=member).status_code == 404
    assert client.post(dp + "/retry", headers=member).status_code == 404
    assert (
        client.post(
            path + "/documents", headers=member, files={"file": ("a.txt", b"x")}
        ).status_code
        == 404
    )
    assert client.get(path, headers={}).status_code == 401
    duplicate = client.post(
        path + "/documents", headers=admin, files={"file": ("renamed.txt", b"Public knowledge.")}
    )
    assert duplicate.status_code == 409
    # Same name, different content is allowed; identity comes from content and UUID.
    upload(client, path, admin, b"Other content.")
    assert len(client.get(path + "/documents?limit=1&offset=1", headers=admin).json()) == 1
    assert client.get(path + "/documents?limit=101", headers=admin).status_code == 422
    assert (
        client.post(
            path + "/documents", headers=admin, files={"file": ("../evil.txt", b"x")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/documents", headers=admin, files={"file": ("evil.exe", b"x")}
        ).status_code
        == 415
    )


def test_batch_failure_atomic_retry_and_accounting(knowledge_client, identity_db):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers, b"a" * 380)
    dp = path + "/documents/" + doc["id"]
    provider = client.app.state.embedding_provider
    original = provider.client._transport
    calls = 0

    async def transport(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx2.Response(429, text="private upstream diagnostic")
        return await original.handle_async_request(request)

    provider.client._transport = httpx2.MockTransport(transport)
    ingest(client, identity_db)
    result = client.get(dp, headers=headers).json()
    assert result["status"] == "failed" and result["error_code"] == "embedding_rate_limited"
    assert client.get(dp + "/chunks", headers=headers).json() == []
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    first = client.get(dp + "/jobs", headers=headers).json()[0]
    assert first["completed_batches"] == 1 and first["total_tokens"] == 3
    assert "private" not in str(first)
    retried = client.post(dp + "/retry", headers=headers)
    assert retried.status_code == 202 and retried.json()["version"] == 2
    assert client.post(dp + "/retry", headers=headers).status_code == 409
    provider.client._transport = original
    ingest(client, identity_db)
    assert client.get(dp, headers=headers).json()["chunk_count"] == 5
    assert len(client.get(dp + "/chunks", headers=headers).json()) == 5


def test_claim_concurrency_expiry_and_late_worker(knowledge_client, identity_db):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(lambda _: worker.claim(identity_db, client.app.state.settings), range(2))
        )
    assert claims.count(None) == 1
    job_id = next(c for c in claims if c is not None)
    with identity_db.begin() as db:
        db.execute(
            update(IngestionJob).values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert worker.recover(identity_db) == 1
    assert worker.recover(identity_db) == 0
    dp = path + "/documents/" + doc["id"]
    assert client.get(dp, headers=headers).json()["error_code"] == "worker_interrupted"
    client.post(dp + "/retry", headers=headers)
    ingest(client, identity_db)
    asyncio.run(
        worker.process(
            identity_db, job_id, client.app.state.settings, client.app.state.embedding_provider
        )
    )
    assert client.get(dp, headers=headers).json()["status"] == "ready"


@pytest.mark.parametrize("delete_kb", [False, True])
def test_delete_during_model_call_cannot_resurrect(knowledge_client, identity_db, delete_kb):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers)
    dp = path + "/documents/" + doc["id"]
    provider = client.app.state.embedding_provider
    original = provider.client._transport

    async def delete_during_http(request):
        assert client.delete(path if delete_kb else dp, headers=headers).status_code == 204
        return await original.handle_async_request(request)

    provider.client._transport = httpx2.MockTransport(delete_during_http)
    ingest(client, identity_db)
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        assert db.scalar(select(IngestionJob.status)) == "cancelled"
        assert db.scalar(select(Document.status)) == "deleted"
    assert client.get(dp, headers=headers).status_code == 404
    assert worker.cleanup(identity_db, client.app.state.settings) == 1
    assert worker.cleanup(identity_db, client.app.state.settings) == 0
    assert not file_path(client.app.state.settings.document_storage_path, UUID(doc["id"])).exists()


@pytest.mark.parametrize(
    "content,filename,code",
    [
        (b" \n", "empty.txt", "empty_document"),
        (b"\xff", "wrong.txt", "document_encoding_error"),
        (text_pdf(blank=True), "scan.pdf", "pdf_no_text"),
        (b"broken", "broken.pdf", "invalid_pdf"),
    ],
)
def test_parse_failures_visible_without_vectors(
    knowledge_client, identity_db, content, filename, code
):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers, content, filename)
    ingest(client, identity_db)
    result = client.get(path + "/documents/" + doc["id"], headers=headers).json()
    assert result["status"] == "failed" and result["error_code"] == code


def test_model_mismatch_checksum_and_inactive_owner(knowledge_client, identity_db):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers)
    dp = path + "/documents/" + doc["id"]
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"embedding_model": "different-model"}
    )
    ingest(client, identity_db)
    assert client.get(dp, headers=headers).json()["error_code"] == "embedding_space_mismatch"
    assert client.post(dp + "/retry", headers=headers).status_code == 409
    client.app.state.settings = client.app.state.embedding_provider.settings
    assert client.post(dp + "/retry", headers=headers).status_code == 202
    file_path(client.app.state.settings.document_storage_path, UUID(doc["id"])).write_bytes(
        b"changed"
    )
    ingest(client, identity_db)
    assert client.get(dp, headers=headers).json()["error_code"] == "document_checksum_mismatch"
    client.post(dp + "/retry", headers=headers)
    with identity_db.begin() as db:
        db.execute(update(User).values(is_active=False))
    ingest(client, identity_db)
    with identity_db() as db:
        assert db.get(Document, UUID(doc["id"])).error_code == "owner_inactive"


def test_redis_failure_and_file_compensation(knowledge_client, identity_db, monkeypatch):
    from redis.exceptions import ConnectionError

    client = knowledge_client
    path, headers = kb_and_headers(client)
    queue = WakeupQueue(client.app.state.settings)

    def unavailable(*args, **kwargs):
        raise ConnectionError("secret Redis URL must not escape")

    monkeypatch.setattr(queue.client, "pipeline", unavailable)
    monkeypatch.setattr(client.app.state.knowledge_queue, "notify", queue.notify)
    doc = upload(client, path, headers)
    ingest(client, identity_db)
    assert client.get(path + "/documents/" + doc["id"], headers=headers).json()["status"] == "ready"
    root = client.app.state.settings.document_storage_path
    orphan = file_path(root, uuid4())
    orphan.write_bytes(b"unreferenced old upload")
    os.utime(orphan, (time.time() - 7200, time.time() - 7200))
    assert worker.cleanup(identity_db, client.app.state.settings) == 1
    assert not orphan.exists()
    queue.close()


def test_oversized_upload_rejected_before_spooling(knowledge_client):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    response = client.post(
        path + "/documents",
        headers={**headers, "Content-Length": "99999999"},
        content=b"small actual content",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_size_limit"


def test_streamed_body_limit_without_content_length(knowledge_client):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    response = client.post(
        path + "/documents",
        headers={**headers, "Content-Type": "multipart/form-data; boundary=p4"},
        content=iter(
            [
                b'--p4\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n\r\n',
                b"x" * 5_100_000,
                b"\r\n--p4--\r\n",
            ]
        ),
    )
    assert response.status_code == 413
    assert list(client.app.state.settings.document_storage_path.iterdir()) == []


def test_ready_delete_and_failed_file_cleanup_retries(knowledge_client, identity_db, monkeypatch):
    from pathlib import Path

    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers)
    ingest(client, identity_db)
    source = file_path(client.app.state.settings.document_storage_path, UUID(doc["id"]))
    assert client.delete(path + "/documents/" + doc["id"], headers=headers).status_code == 204
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    original = Path.unlink

    def unavailable(target, *args, **kwargs):
        if target == source:
            raise PermissionError("file temporarily unavailable")
        return original(target, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "unlink", unavailable)
        assert worker.cleanup(identity_db, client.app.state.settings) == 0
    with identity_db() as db:
        assert db.get(Document, UUID(doc["id"])).file_removed_at is None
    assert worker.cleanup(identity_db, client.app.state.settings) == 1
    assert not source.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_upload_commit_failure_preserves_uncertain_file(
    knowledge_client, identity_db, monkeypatch, committed
):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    original = Session.commit

    def interrupted_commit(session):
        if committed:
            original(session)
        raise OperationalError("COMMIT", {}, Exception("private database detail"))

    with monkeypatch.context() as patched:
        patched.setattr(Session, "commit", interrupted_commit)
        response = client.post(
            path + "/documents", headers=headers, files={"file": ("public.txt", b"public text")}
        )
    assert response.status_code == 503 and "private" not in response.text
    root = client.app.state.settings.document_storage_path
    files = list(root.glob("*.raw"))
    assert len(files) == 1
    for source in files:
        os.utime(source, (time.time() - 7200, time.time() - 7200))
    assert worker.cleanup(identity_db, client.app.state.settings) == (0 if committed else 1)
    if committed:
        ingest(client, identity_db)
        with identity_db() as db:
            assert db.scalar(select(Document.status)) == "ready"


def test_actual_worker_process_termination_recovery(
    knowledge_client, identity_db, p2_settings, tmp_path
):
    client = knowledge_client
    path, headers = kb_and_headers(client)
    doc = upload(client, path, headers)
    marker = tmp_path / "claimed.txt"
    environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENTHUB_")}
    environment.update(
        {
            "AGENTHUB_DATABASE_HOST": p2_settings.database_host,
            "AGENTHUB_DATABASE_PORT": str(p2_settings.database_port),
            "AGENTHUB_DATABASE_NAME": p2_settings.database_name,
            "AGENTHUB_DATABASE_USER": p2_settings.database_user,
            "AGENTHUB_DATABASE_PASSWORD": p2_settings.database_password.get_secret_value(),
        }
    )
    script = "\n".join(
        [
            "import sys, time",
            "from pathlib import Path",
            "from agenthub.core.config import Settings",
            "from agenthub.db.session import create_database_engine, create_session_factory",
            "from agenthub.knowledge.worker import claim",
            "s = Settings(_env_file=None)",
            "e = create_database_engine(s)",
            "job_id = claim(create_session_factory(e), s)",
            "Path(sys.argv[1]).write_text(str(job_id))",
            "time.sleep(60)",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(marker)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        process.terminate()
        process.wait(timeout=5)
    with identity_db.begin() as db:
        db.execute(
            update(IngestionJob).values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert worker.recover(identity_db) == 1
    result = client.get(path + "/documents/" + doc["id"], headers=headers).json()
    assert result["status"] == "failed" and result["error_code"] == "worker_interrupted"
