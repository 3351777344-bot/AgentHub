# ruff: noqa: F811 -- pytest fixtures imported for reuse
"""Explicitly opted-in, paid DeepSeek calls with fixed public text and real PostgreSQL."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_identity_integration import (  # noqa: F401
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    sign_in,
)
from test_identity_integration import (
    pytestmark as database_marks,
)

from agenthub.chat.models import ModelCall
from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.main import create_app

pytestmark = [
    *database_marks,
    pytest.mark.skipif(
        os.getenv("AGENTHUB_RUN_P3_MODEL_ACCEPTANCE") != "1",
        reason="Paid model test: explicitly enable AGENTHUB_RUN_P3_MODEL_ACCEPTANCE=1",
    ),
]


def test_real_deepseek_authenticated_chat_and_history(identity_client, identity_db, p2_settings):
    configured = Settings()
    assert configured.model_base_url == "https://api.deepseek.com"
    assert configured.model_name == "deepseek-flash"
    assert configured.model_api_key.get_secret_value().strip()
    client = identity_client
    settings = p2_settings.model_copy(
        update={
            "model_base_url": configured.model_base_url,
            "model_name": configured.model_name,
            "model_api_key": configured.model_api_key,
            "model_max_tokens": 128,
            "model_thinking_mode": "disabled",
            "model_include_usage": True,
        }
    )
    client.app.state.settings = settings
    client.app.state.chat_provider = ChatProvider(settings, client.app.state.chat_provider.client)
    headers = sign_in(client)
    created = client.post(
        "/api/v1/conversations", headers=headers, json={"title": "Public API test"}
    )
    assert created.status_code == 201
    path = f"/api/v1/conversations/{created.json()['id']}/messages"
    ordinary = client.post(
        path,
        headers=headers,
        json={
            "content": "Public API test: reply with the word OK only.",
        },
    )
    assert ordinary.status_code == 200
    assert ordinary.json()["status"] == "completed" and ordinary.json()["content"].strip()
    streamed = client.post(
        path + "?stream=true",
        headers=headers,
        json={
            "content": "Public API test: reply with the word READY only.",
        },
    )
    assert streamed.status_code == 200
    assert "event: delta" in streamed.text and "event: done" in streamed.text
    assert "event: error" not in streamed.text
    with identity_db() as db:
        calls = list(db.scalars(select(ModelCall)))
        assert len(calls) == 2
        assert all(c.prompt_tokens and c.completion_tokens for c in calls)
        print("REAL_MODEL_USAGE", [(c.prompt_tokens, c.completion_tokens) for c in calls])
    # New app, engine and HTTP client read the same DB history with no new model call.
    with TestClient(create_app(p2_settings)) as restarted:
        history = restarted.get(path, headers=headers)
        assert history.status_code == 200 and len(history.json()) == 4
        assert all(m["status"] == "completed" for m in history.json())
