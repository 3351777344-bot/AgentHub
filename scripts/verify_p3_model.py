"""Two real DeepSeek probes using only fixed public text; no automatic retries.

This verifies the provider adapter, not authenticated API/persistence. Run manually
after configuring .env. Each probe caps output at 128 tokens with thinking disabled.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx2

from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError

ROOT = Path(__file__).resolve().parents[1]


async def verify() -> int:
    settings = Settings()
    # Explicit destination/model guards prevent sending credentials or tests to
    # another configured provider by accident. No project files enter the prompt.
    if (
        settings.model_base_url != "https://api.deepseek.com"
        or settings.model_name != "deepseek-flash"
    ):
        print("Expected the approved DeepSeek base URL and deepseek-flash model")
        return 2
    if not settings.model_api_key.get_secret_value().strip():
        print("Model API key is missing; no request sent")
        return 2
    settings = settings.model_copy(
        update={
            "model_max_tokens": 128,
            "model_thinking_mode": "disabled",
            "model_include_usage": True,
        }
    )
    report: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(),
        "model": settings.model_name,
        "scope": "provider adapter only",
        "max_requests": 2,
        "max_output_tokens_per_request": 128,
    }
    results: list[dict[str, object]] = []
    report["results"] = results
    output = ROOT / "data" / "acceptance" / f"p3-model-{uuid4().hex}.json"
    try:
        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            provider = ChatProvider(settings, client)
            for stream in (False, True):
                text = ""
                prompt_tokens = completion_tokens = None
                async with asyncio.timeout(settings.model_timeout_seconds):
                    async for chunk in provider.generate(
                        [
                            {
                                "role": "user",
                                "content": "Public API test: reply with the word OK only.",
                            }
                        ],
                        stream=stream,
                    ):
                        text += chunk.text
                        if chunk.prompt_tokens is not None:
                            prompt_tokens = chunk.prompt_tokens
                        if chunk.completion_tokens is not None:
                            completion_tokens = chunk.completion_tokens
                if not text.strip():
                    raise AppError(502, "empty_model_reply", "Model returned no text")
                result: dict[str, object] = {
                    "stream": stream,
                    "passed": True,
                    "response_characters": len(text),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                results.append(result)
                print(json.dumps(result))
    except (AppError, TimeoutError) as exc:
        report["error_code"] = exc.code if isinstance(exc, AppError) else "model_timeout"
        print(json.dumps({"passed": False, "code": report["error_code"]}))
        return 1
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(verify()))
