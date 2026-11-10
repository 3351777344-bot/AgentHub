"""Protocol and failure tests use an explicit HTTP transport double; no paid calls."""

import asyncio
import copy
import json
import math

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.embeddings.provider import EmbeddingProvider

URL = "https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def configured(**overrides):
    return Settings(
        _env_file=None,
        **{
            "embedding_base_url": URL,
            "embedding_model": "qwen3.7-text-embedding-flash",
            "embedding_api_key": SecretStr("test-only-key"),
            **overrides,
        },
    )


def response_body():
    return {
        "model": "qwen3.7-text-embedding-flash",
        "data": [
            {"index": 1, "embedding": [0.0, 4.0] + [0.0] * 1022},
            {"index": 0, "embedding": [3.0, 0.0] + [0.0] * 1022},
        ],
        "usage": {"total_tokens": 12},
    }


def run(handler, texts=None, settings=None):
    async def execute():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await EmbeddingProvider(settings or configured(), client).embed(
                texts if texts is not None else ["public one", "public two"]
            )

    return asyncio.run(execute())


def test_payload_reordering_normalization_and_total_usage():
    def handler(request):
        assert request.url.path == "/compatible-mode/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert json.loads(request.content) == {
            "model": "qwen3.7-text-embedding-flash",
            "input": ["public one", "public two"],
            "dimensions": 1024,
            "encoding_format": "float",
        }
        return httpx2.Response(200, json=response_body())

    result = run(handler)
    assert result.vectors[0][0] == 1.0 and result.vectors[1][1] == 1.0
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in result.vectors)
    assert result.total_tokens == 12 and result.duration_ms >= 0


@pytest.mark.parametrize(
    "fault",
    [
        "dimension",
        "count",
        "duplicate",
        "range",
        "bool_index",
        "nan",
        "inf",
        "bool_value",
        "string_value",
        "zero",
        "model",
        "usage",
    ],
)
def test_invalid_outputs_rejected_as_whole_batch(fault):
    body = copy.deepcopy(response_body())
    if fault == "dimension":
        body["data"][0]["embedding"].pop()
    elif fault == "count":
        body["data"].pop()
    elif fault == "duplicate":
        body["data"][0]["index"] = 0
    elif fault == "range":
        body["data"][0]["index"] = 2
    elif fault == "bool_index":
        body["data"][0]["index"] = True
    elif fault in ("nan", "inf", "bool_value", "string_value"):
        body["data"][0]["embedding"][0] = {
            "nan": float("nan"),
            "inf": float("inf"),
            "bool_value": True,
            "string_value": "1",
        }[fault]
    elif fault == "zero":
        body["data"][0]["embedding"] = [0.0] * 1024
    elif fault == "model":
        body["model"] = "different-space"
    elif fault == "usage":
        body["usage"]["total_tokens"] = -1
    # Raw JSON lets us exercise nonstandard NaN/Infinity received from an upstream.
    with pytest.raises(AppError) as error:
        run(lambda r: httpx2.Response(200, content=json.dumps(body)))
    assert error.value.code == "embedding_invalid_response"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "embedding_upstream_error"),
        (429, "embedding_rate_limited"),
        (500, "embedding_upstream_error"),
        (302, "embedding_upstream_error"),
    ],
)
def test_error_redaction_and_no_retries(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(status, text="private-credentials")

    with pytest.raises(AppError) as error:
        run(handler)
    assert len(calls) == 1 and error.value.code == code
    assert "private" not in error.value.message


@pytest.mark.parametrize("texts", [[], [" "], ["x" * 8001], ["a"] * 11])
def test_invalid_input_never_calls_transport(texts):
    def handler(request):
        pytest.fail("Invalid input must not leave the process")

    with pytest.raises(AppError) as error:
        run(handler, texts=texts)
    assert error.value.code == "embedding_invalid_input"


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"error": {"code": "insufficient_quota", "message": "private-key"}},
            "embedding_quota_exhausted",
        ),
        (
            {"error": {"code": "access_denied", "message": "private-key"}},
            "embedding_upstream_error",
        ),
        ([], "embedding_upstream_error"),
    ],
)
def test_quota_error_is_actionable_and_redacted(body, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(403, json=body)

    with pytest.raises(AppError) as error:
        run(handler)
    assert error.value.code == expected and "private" not in error.value.message
    assert len(calls) == 1


def test_timeout_unconfigured_and_missing_usage():
    def handler(request):
        raise httpx2.ReadTimeout("secret", request=request)

    with pytest.raises(AppError) as error:
        run(handler)
    assert error.value.code == "embedding_timeout"
    with pytest.raises(AppError) as error:
        run(handler, settings=configured(embedding_api_key=SecretStr("")))
    assert error.value.code == "embedding_not_configured"
    body = response_body()
    body.pop("usage")
    assert run(lambda r: httpx2.Response(200, json=body)).total_tokens is None
    assert "test-only-key" not in repr(configured())


def test_total_timeout_and_network_failure():
    async def slow(request):
        await asyncio.sleep(2)
        return httpx2.Response(200, json=response_body())

    with pytest.raises(AppError) as error:
        run(slow, settings=configured(embedding_timeout_seconds=1))
    assert error.value.code == "embedding_timeout"

    def disconnected(request):
        raise httpx2.ConnectError("private-endpoint", request=request)

    with pytest.raises(AppError) as error:
        run(disconnected)
    assert error.value.code == "embedding_connection_error"


def test_invalid_json_and_response_size_limit():
    for content in (b"{invalid", b"x" * 4_000_001):
        with pytest.raises(AppError) as error:
            run(lambda request, body=content: httpx2.Response(200, content=body))
        assert error.value.code == "embedding_invalid_response"


def test_lifespan_exposes_embedding_without_network_or_key(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert isinstance(client.app.state.embedding_provider, EmbeddingProvider)
        with pytest.raises(AppError) as error:
            client.app.state.embedding_provider.check_configured()
        assert error.value.code == "embedding_not_configured"


@pytest.mark.parametrize(
    "url",
    [
        "http://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "https://test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        URL + "?key=secret",
        "https://test.cn-beijing.maas.aliyuncs.com.evil.test/compatible-mode/v1",
        URL + "/embeddings",
    ],
)
def test_reject_wrong_destination(url):
    with pytest.raises(ValidationError):
        configured(embedding_base_url=url)
