"""HTTP transport doubles: these tests never contact a model provider."""

import asyncio
import json

import httpx2 as httpx
import pytest

from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError


def run_provider(handler, stream=False, thinking_mode="provider_default"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ChatProvider(
                Settings(
                    _env_file=None,
                    model_base_url="https://model.test/v1",
                    model_name="test-model",
                    model_thinking_mode=thinking_mode,
                ),
                client,
            )
            return [
                c
                async for c in provider.generate(
                    [{"role": "user", "content": "hello"}], stream=stream
                )
            ]

    return asyncio.run(run())


def test_nonstream_request_and_usage():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["max_tokens"] == 1024
        assert "thinking" not in json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    chunks = run_provider(handler)
    assert chunks[0].text == "answer" and chunks[0].prompt_tokens == 3


def test_explicit_nonthinking_extension():
    def handler(request):
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    assert run_provider(handler, thinking_mode="disabled")[0].text == "OK"


def test_stream_usage_and_terminal():
    body = (
        'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
        "data: [DONE]\n\n"
    )
    chunks = run_provider(lambda r: httpx.Response(200, text=body), stream=True)
    assert "".join(c.text for c in chunks) == "hi"
    assert chunks[-1].prompt_tokens == 5


@pytest.mark.parametrize(
    "status,code",
    [(401, "model_upstream_error"), (429, "model_rate_limited"), (500, "model_upstream_error")],
)
def test_upstream_errors_are_sanitized_and_not_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret-key-and-provider-details")

    with pytest.raises(AppError) as error:
        run_provider(handler)
    assert error.value.code == code and "secret" not in error.value.message
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "data: [DONE]\n\n",
        "data: {bad}\n\n",
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
    ],
)
def test_incomplete_stream_rejected(body):
    with pytest.raises(AppError) as error:
        run_provider(lambda r: httpx.Response(200, text=body), stream=True)
    assert error.value.code == "model_invalid_response"


def test_timeout_and_unconfigured():
    def handler(request):
        raise httpx.ReadTimeout("sensitive", request=request)

    with pytest.raises(AppError) as error:
        run_provider(handler)
    assert error.value.code == "model_timeout"
    with pytest.raises(AppError) as error:
        ChatProvider(Settings(_env_file=None), None).check_configured()
    assert error.value.code == "model_not_configured"
