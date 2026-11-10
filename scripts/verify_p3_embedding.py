"""Two bounded real Embedding requests containing only fixed public test text.

No files, account data, credentials or user-supplied text enter the input. This
checks provider integration and a tiny semantic sanity case, not RAG quality.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx2

from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.embeddings.provider import EmbeddingProvider

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    "苹果和香蕉是常见的水果。",
    "PostgreSQL 是关系型数据库。",
    "Redis 支持键值存储和缓存。",
]
QUERIES = ["哪些是常见水果？", "哪一种是关系型数据库？"]


async def verify(*, check_only: bool = False) -> int:
    settings = Settings()
    async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
        provider = EmbeddingProvider(settings, client)
        try:
            provider.check_configured()
            if (
                settings.embedding_model != "qwen3.7-text-embedding-flash"
                or settings.embedding_dimensions != 1024
            ):
                raise AppError(
                    422,
                    "acceptance_config_mismatch",
                    "Expected the approved model and 1024 dimensions",
                )
            if settings.embedding_batch_size < 3 or settings.embedding_max_characters < 30:
                raise AppError(
                    422,
                    "acceptance_config_mismatch",
                    "Acceptance requires batch size >=3 and text limit >=30",
                )
        except AppError as exc:
            print(json.dumps({"ready": False, "code": exc.code}))
            return 2
        if check_only:
            print(json.dumps({"ready": True, "requests_sent": 0}))
            return 0
        # Fixed 5 short texts and two POSTs; never retry or read project documents.
        assert sum(len(t.encode("utf-8")) for t in DOCUMENTS + QUERIES) < 1024
        report: dict[str, object] = {
            "at": datetime.now(UTC).isoformat(),
            "model": settings.embedding_model,
            "dimensions": 1024,
            "normalization": "l2",
            "max_requests": 2,
            "budget_cny": "10",
            "passed": False,
        }
        output = ROOT / "data" / "acceptance" / f"p3-embedding-{uuid4().hex}.json"
        tokens: list[int | None] = []
        try:
            documents = await provider.embed(DOCUMENTS)
            tokens.append(documents.total_tokens)
            queries = await provider.embed(QUERIES)
            tokens.append(queries.total_tokens)
            top_indices = [
                max(
                    range(len(documents.vectors)),
                    key=lambda index: sum(
                        a * b for a, b in zip(query, documents.vectors[index], strict=True)
                    ),
                )
                for query in queries.vectors
            ]
            report.update(
                {
                    "requests_completed": 2,
                    "total_tokens_per_request": tokens,
                    "duration_ms": [documents.duration_ms, queries.duration_ms],
                    "top_document_indices": top_indices,
                    "passed": top_indices == [0, 1],
                }
            )
            if all(t is not None for t in tokens):
                cost = (
                    Decimal(sum(t for t in tokens if t is not None)) * Decimal("0.125") / 1_000_000
                )
                report["estimated_cost_cny"] = str(cost)
                # Rate is an explicitly documented snapshot, not a billing API.
                report["price_reference"] = "2026-11-10 Beijing sync: CNY 0.125/1M input tokens"
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["passed"] else 1
        except AppError as exc:
            report.update({"error_code": exc.code, "total_tokens_per_request": tokens})
            print(json.dumps({"passed": False, "code": exc.code}))
            return 1
        finally:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check config without any network calls"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(verify(check_only=args.check)))
