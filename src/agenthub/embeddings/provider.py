"""Beijing Model Studio compatible embeddings; errors never expose upstream bodies."""

import asyncio
import json
import math
import time
from dataclasses import dataclass

import httpx2

from agenthub.core.config import Settings
from agenthub.core.errors import AppError


@dataclass(frozen=True)
class EmbeddingResult:
    model: str
    dimensions: int
    vectors: list[list[float]]
    total_tokens: int | None
    duration_ms: int


class EmbeddingProvider:
    def __init__(self, settings: Settings, client: httpx2.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def check_configured(self) -> None:
        if not (
            self.settings.embedding_base_url
            and self.settings.embedding_model
            and self.settings.embedding_api_key.get_secret_value().strip()
        ):
            raise AppError(503, "embedding_not_configured", "Embedding service is not configured")

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """One bounded batch, no implicit retries/splitting, vectors in original input order.

        Fail the entire batch on invalid output. The future worker owns chunking,
        persistence and retry policy; this adapter never writes partial results.
        """
        self.check_configured()
        if not 1 <= len(texts) <= self.settings.embedding_batch_size or any(
            not text.strip() or len(text) > self.settings.embedding_max_characters for text in texts
        ):
            raise AppError(422, "embedding_invalid_input", "Invalid embedding batch or text length")
        started = time.monotonic()
        payload = {
            "model": self.settings.embedding_model,
            "input": texts,
            "dimensions": self.settings.embedding_dimensions,
            "encoding_format": "float",
        }
        try:
            async with asyncio.timeout(self.settings.embedding_timeout_seconds):
                async with self.client.stream(
                    "POST",
                    self.settings.embedding_base_url + "/embeddings",
                    json=payload,
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.embedding_api_key.get_secret_value()
                    },
                    timeout=self.settings.embedding_timeout_seconds,
                ) as response:
                    if response.status_code == 429:
                        raise AppError(
                            429, "embedding_rate_limited", "Embedding rate limit exceeded"
                        )
                    if response.status_code == 403:
                        # Read only a bounded error body to classify the actionable
                        # quota error; never expose its message, input or credentials.
                        error_body = bytearray()
                        async for part in response.aiter_bytes():
                            if len(error_body) + len(part) > 8192:
                                break
                            error_body.extend(part)
                        try:
                            error = json.loads(error_body).get("error", {})
                        except (ValueError, AttributeError):
                            error = {}
                        if isinstance(error, dict) and error.get("code") == "insufficient_quota":
                            raise AppError(
                                503,
                                "embedding_quota_exhausted",
                                "Embedding service quota unavailable",
                            )
                    if not 200 <= response.status_code < 300:
                        raise AppError(
                            502, "embedding_upstream_error", "Embedding service request failed"
                        )
                    content = bytearray()
                    async for part in response.aiter_bytes():
                        content.extend(part)
                        if len(content) > 4_000_000:
                            raise ValueError
                    body = json.loads(content)
            vectors, total_tokens = self._parse(body, len(texts))
        except (httpx2.TimeoutException, TimeoutError):
            raise AppError(504, "embedding_timeout", "Embedding service timed out") from None
        except httpx2.HTTPError:
            raise AppError(
                502, "embedding_connection_error", "Embedding service connection failed"
            ) from None
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise AppError(
                502, "embedding_invalid_response", "Invalid embedding service response"
            ) from None
        return EmbeddingResult(
            self.settings.embedding_model,
            self.settings.embedding_dimensions,
            vectors,
            total_tokens,
            int((time.monotonic() - started) * 1000),
        )

    def _parse(self, body: object, count: int) -> tuple[list[list[float]], int | None]:
        if not isinstance(body, dict) or body.get("model") != self.settings.embedding_model:
            raise ValueError
        data = body["data"]
        if not isinstance(data, list) or len(data) != count:
            raise ValueError
        indexed: dict[int, list[float]] = {}
        for item in data:
            index, vector = item["index"], item["embedding"]
            if type(index) is not int or not 0 <= index < count or index in indexed:
                raise ValueError
            if not isinstance(vector, list) or len(vector) != self.settings.embedding_dimensions:
                raise ValueError
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise ValueError
            # Scale before normalizing to avoid overflow on malformed extreme values.
            scale = max(abs(value) for value in vector)
            if scale == 0:
                raise ValueError
            values = [float(value / scale) for value in vector]
            norm = math.sqrt(sum(value * value for value in values))
            indexed[index] = [value / norm for value in values]
        usage = body.get("usage") or {}
        total_tokens = usage.get("total_tokens")
        if total_tokens is not None and (type(total_tokens) is not int or total_tokens < 0):
            raise ValueError
        return [indexed[index] for index in range(count)], total_tokens
