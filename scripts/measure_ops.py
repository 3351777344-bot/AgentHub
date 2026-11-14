"""Bounded HTTP benchmark for the isolated release workload."""

import argparse
import math
import os
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2


def measure(
    client: httpx2.Client, path: str, requests: int, concurrency: int, p95_limit: float
) -> None:
    def fetch(_: int) -> float:
        start = time.perf_counter()
        response = client.get(path)
        response.raise_for_status()
        return (time.perf_counter() - start) * 1000

    fetch(0)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as workers:
        samples = sorted(workers.map(fetch, range(requests)))
    elapsed = time.perf_counter() - start
    p50, p95 = (samples[math.ceil(len(samples) * percentile) - 1] for percentile in (0.5, 0.95))
    print(
        f"{path}: {requests}/{requests} OK, {requests / elapsed:.1f}/s,"
        f" p50={p50:.1f}ms p95={p95:.1f}ms, max={samples[-1]:.1f}ms"
    )
    assert p95 <= p95_limit, f"P95 exceeded {p95_limit}ms"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", choices=("http://127.0.0.1:18101", "https://agenthub.test:18443"))
    parser.add_argument("--requests", type=int, default=200, choices=range(1, 1001))
    parser.add_argument("--concurrency", type=int, default=8, choices=range(1, 17))
    parser.add_argument("--p95-ms", type=float, default=150)
    parser.add_argument("--certificate", type=Path)
    args = parser.parse_args()
    verify = ssl.create_default_context(cafile=str(args.certificate)) if args.certificate else True
    with httpx2.Client(base_url=args.base, trust_env=False, timeout=8, verify=verify) as client:
        measure(client, "/health/ready", args.requests, args.concurrency, args.p95_ms)
        token = os.getenv("AGENTHUB_OPS_BEARER")
        if token:
            client.headers["Authorization"] = f"Bearer {token}"
            measure(client, "/api/v1/auth/me", args.requests, args.concurrency, args.p95_ms)
            measure(
                client,
                "/api/v1/operations/metrics",
                args.requests,
                args.concurrency,
                args.p95_ms,
            )


if __name__ == "__main__":
    main()
