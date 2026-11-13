"""Native tool-call messages over the configured Chat Completions endpoint."""

import json
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from agenthub.chat.provider import ChatProvider
from agenthub.core.errors import AppError
from agenthub.tools.contracts import ARGUMENT_BYTES, RESULT_BYTES, bounded_json
from agenthub.tools.schemas import ToolDescription


class Function(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    arguments: str = Field(max_length=ARGUMENT_BYTES)


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"]
    function: Function


class Assistant(BaseModel):
    role: Literal["assistant"]
    content: str | None = Field(default=None, max_length=65536)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=8)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_calls(cls, value: object) -> object:
        return [] if value is None else value


class Usage(BaseModel):
    prompt_tokens: int = Field(strict=True, ge=0, le=10_000_000)
    completion_tokens: int = Field(strict=True, ge=0, le=100_000)


class Choice(BaseModel):
    finish_reason: Literal["stop", "tool_calls", "length"]
    message: Assistant


class Completion(BaseModel):
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: Usage


def tool_alias(name: str, version: int) -> str:
    return f"{name}__v{version}"


class AgentProvider(ChatProvider):
    async def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDescription],
        max_tokens: int,
    ) -> Completion:
        self.check_configured()
        try:
            bounded_json(messages, self.settings.model_context_characters * 4 + 65536)
        except ValueError:
            raise AppError(422, "agent_context_limit", "Agent context exceeds its limit") from None
        if len(json.dumps(messages, ensure_ascii=False)) > self.settings.model_context_characters:
            raise AppError(422, "agent_context_limit", "Agent context exceeds its limit")
        payload: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if (
            self.settings.model_thinking_mode == "disabled"
            or urlsplit(self.settings.model_base_url).hostname == "api.deepseek.com"
        ):
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_alias(tool.name, tool.version),
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
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
                if response.status_code != 200:
                    raise AppError(502, "model_upstream_error", "Model service request failed")
                raw = bytearray()
                async for part in response.aiter_bytes(chunk_size=8192):
                    raw.extend(part)
                    if len(raw) > RESULT_BYTES:
                        raise ValueError
                completion = Completion.model_validate_json(raw)
                choice = completion.choices[0]
                message = choice.message
                bounded_json(message.model_dump(mode="json"), RESULT_BYTES)
                if choice.finish_reason == "tool_calls" and (
                    not message.tool_calls
                    or len({call.id for call in message.tool_calls}) != len(message.tool_calls)
                ):
                    raise ValueError
                if choice.finish_reason == "stop" and (message.tool_calls or not message.content):
                    raise ValueError
                return completion
        except httpx.TimeoutException:
            raise AppError(504, "model_timeout", "Model service timed out") from None
        except httpx.HTTPError:
            raise AppError(
                502, "model_connection_error", "Model service connection failed"
            ) from None
        except (ValueError, ValidationError, RecursionError):
            raise AppError(
                502, "model_invalid_response", "Invalid model service response"
            ) from None
