"""Text-only OpenAI-compatible Chat Completions adapter, with no automatic retries.

Retrying an uncertain POST can duplicate billing. Capabilities beyond text/streaming
are deliberately not promised until verified for the configured provider.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx2 as httpx

from agenthub.core.config import Settings
from agenthub.core.errors import AppError


@dataclass
class Chunk:
    text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def check_configured(self) -> None:
        if not self.settings.model_base_url or not self.settings.model_name:
            raise AppError(503, "model_not_configured", "Model service is not configured")

    async def generate(
        self, messages: list[dict[str, str]], *, stream: bool
    ) -> AsyncIterator[Chunk]:
        self.check_configured()
        payload: dict[str, object] = {
            "model": self.settings.model_name,
            "messages": messages,
            "max_tokens": self.settings.model_max_tokens,
            "stream": stream,
        }
        if stream and self.settings.model_include_usage:
            payload["stream_options"] = {"include_usage": True}
        if self.settings.model_thinking_mode == "disabled":
            payload["thinking"] = {"type": "disabled"}
        key = self.settings.model_api_key.get_secret_value()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with self.client.stream(
                "POST",
                self.settings.model_base_url + "/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.settings.model_timeout_seconds,
            ) as response:
                if response.status_code == 429:
                    raise AppError(429, "model_rate_limited", "Model rate limit exceeded")
                if response.status_code >= 400:
                    raise AppError(502, "model_upstream_error", "Model service request failed")
                if not stream:
                    body = json.loads(await response.aread())
                    text = body["choices"][0]["message"]["content"]
                    if not isinstance(text, str):
                        raise ValueError
                    yield self._chunk(text, body.get("usage"))
                    return
                finished = False
                async for line in response.aiter_lines():
                    if len(line) > 262144:
                        raise ValueError
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        if not finished:
                            raise ValueError
                        return
                    body = json.loads(data)
                    if "error" in body:
                        raise ValueError
                    choices = body.get("choices", [])
                    text = ""
                    if choices:
                        choice = choices[0]
                        if choice.get("finish_reason") is not None:
                            finished = True
                        text = choice.get("delta", {}).get("content") or ""
                        if not isinstance(text, str):
                            raise ValueError
                    yield self._chunk(text, body.get("usage"))
                # A truncated stream must never become a completed answer.
                raise ValueError
        except httpx.TimeoutException:
            raise AppError(504, "model_timeout", "Model service timed out") from None
        except httpx.HTTPError:
            raise AppError(
                502, "model_connection_error", "Model service connection failed"
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError(
                502, "model_invalid_response", "Invalid model service response"
            ) from None

    @staticmethod
    def _chunk(text: str, usage: object) -> Chunk:
        chunk = Chunk(text=text)
        if isinstance(usage, dict):
            for name in ("prompt_tokens", "completion_tokens"):
                value = usage.get(name)
                if type(value) is int and value >= 0:
                    setattr(chunk, name, value)
        return chunk
