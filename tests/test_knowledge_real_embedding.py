# ruff: noqa: F811 -- explicit isolated database fixtures
"""Opt-in real Flash -> subprocess worker -> PostgreSQL acceptance, fixed public data only."""

import os
import subprocess
import sys
from uuid import UUID

import pytest
from sqlalchemy import select, text
from test_identity_integration import (  # noqa: F401
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)
from test_knowledge_parsing import text_pdf

from agenthub.core.config import Settings
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.knowledge.models import Document, IngestionJob


@pytest.mark.skipif(
    os.getenv("AGENTHUB_RUN_P4_EMBEDDING_ACCEPTANCE") != "1",
    reason="Explicit P4 cloud acceptance opt-in required (three public batches)",
)
def test_real_flash_independent_worker_three_formats(
    identity_client, identity_db, p2_settings, tmp_path
):
    client = identity_client
    cloud = Settings()
    assert cloud.embedding_model == "qwen3.7-text-embedding-flash"
    assert cloud.embedding_dimensions == 1024
    configured = p2_settings.model_copy(
        update={
            **{
                name: getattr(cloud, name)
                for name in (
                    "embedding_base_url",
                    "embedding_api_key",
                    "embedding_model",
                    "embedding_dimensions",
                    "embedding_timeout_seconds",
                    "embedding_batch_size",
                    "embedding_max_characters",
                )
            },
            "document_storage_path": tmp_path,
        }
    )
    client.app.state.settings = configured
    provider = client.app.state.embedding_provider
    client.app.state.embedding_provider = EmbeddingProvider(configured, provider.client)
    headers = sign_in(client)
    response = client.post(
        "/api/v1/knowledge-bases", headers=headers, json={"name": "P4 fixed public acceptance"}
    )
    assert response.status_code == 201, response.text
    path = f"/api/v1/knowledge-bases/{response.json()['id']}/documents"
    docs = []
    for name, content in [
        ("public.txt", b"AgentHub stores document vectors in PostgreSQL with pgvector."),
        ("public.md", b"# Knowledge preparation\nPublic text is split into overlapping chunks."),
        ("public.pdf", text_pdf("Public PDFs have text layers for knowledge ingestion.")),
    ]:
        uploaded = client.post(path, headers=headers, files={"file": (name, content)})
        assert uploaded.status_code == 202, uploaded.text
        docs.append(uploaded.json()["id"])
    # Child has an isolated database/storage target. Only embedding credentials
    # are copied from local settings; no project files or user text are uploaded.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENTHUB_")}
    for field in (
        "environment",
        "database_host",
        "database_port",
        "database_name",
        "database_user",
        "document_storage_path",
        "embedding_base_url",
        "embedding_model",
        "embedding_dimensions",
        "embedding_timeout_seconds",
        "embedding_batch_size",
        "embedding_max_characters",
    ):
        environment["AGENTHUB_" + field.upper()] = str(getattr(configured, field))
    environment["AGENTHUB_DATABASE_PASSWORD"] = configured.database_password.get_secret_value()
    environment["AGENTHUB_EMBEDDING_API_KEY"] = configured.embedding_api_key.get_secret_value()
    for _ in docs:
        result = subprocess.run(
            [sys.executable, "-m", "agenthub.knowledge.worker", "--once"],
            env=environment,
            capture_output=True,
            timeout=90,
        )
        assert result.returncode == 0, "Worker process failed; inspect sanitized job status"
    with identity_db() as db:
        assert all(db.get(Document, UUID(doc_id)).status == "ready" for doc_id in docs)
        assert (
            db.scalar(
                text("select count(*) from document_chunks where vector_dims(embedding)=1024")
            )
            == 3
        )
        tokens = list(db.scalars(select(IngestionJob.total_tokens)))
        assert len(tokens) == 3 and all(t is not None for t in tokens)
        print(f"P4 actual Flash: 3 public files, 3 worker processes, 3 vectors, tokens={tokens}")
    for doc_id in docs:
        assert client.get(path + f"/{doc_id}/chunks", headers=headers).status_code == 200
