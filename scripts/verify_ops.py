"""Smoke-test the packaged API and Redis rate limiting on a disposable stack."""

import argparse
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx2


def run(base: str, limit: int) -> None:
    with httpx2.Client(base_url=base, trust_env=False, timeout=10) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json()["checks"] == {"postgresql": "ok", "redis": "ok"}
        assert "text/html" in client.get("/").headers["content-type"]
        assert client.get("/api/v1/operations/metrics").status_code == 401
        login = "/api/v1/auth/login"
        unsafe = client.options(
            login,
            headers={"Origin": "https://foreign.invalid", "Access-Control-Request-Method": "POST"},
        )
        assert unsafe.status_code == 403 and "access-control-allow-origin" not in unsafe.headers

        def attempt(number: int) -> tuple[int, str | None]:
            marker = uuid4().hex
            response = client.post(
                login,
                json={"username": "disposable_probe", "password": marker},
                headers={"X-Forwarded-For": f"192.0.2.{number + 1}", "X-Request-ID": marker},
            )
            assert response.headers["x-request-id"] == marker
            assert "disposable_probe" not in response.text
            return response.status_code, response.headers.get("retry-after")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(limit + 4)))
        assert sum(status == 401 for status, _ in results) == limit, results
        assert sum(status == 429 for status, _ in results) == 4, results
        assert all(1 <= int(retry) <= 60 for _, retry in results if retry is not None)
        assert all(retry is not None for status, retry in results if status == 429)
    print("PASS packaged smoke, cross-origin rejection, concurrent Redis limit, spoofed headers")


def dependencies(base: str, project: str) -> None:
    def docker(*args: str) -> str:
        return subprocess.check_output(["docker", *args], text=True).strip()

    api = f"{project}-api-1"
    identity = docker("inspect", "--format", "{{.Id}}", api)
    with httpx2.Client(base_url=base, trust_env=False, timeout=10) as client:
        for service in ("redis", "postgres"):
            container = f"{project}-{service}-1"
            docker("stop", container)
            try:
                assert client.get("/health/live").status_code == 200
                assert client.get("/health/ready").status_code == 503
                if service == "redis":
                    login = client.post("/api/v1/auth/login", json={})
                    assert login.status_code == 503
                    assert login.json()["error"]["code"] == "login_limit_unavailable"
            finally:
                docker("start", container)
            now = time.monotonic()
            while client.get("/health/ready").status_code != 200:
                assert time.monotonic() - now < 20
                time.sleep(0.25)
            assert docker("inspect", "--format", "{{.Id}}", api) == identity
            print(f"PASS {service} recovery in {time.monotonic() - now:.2f}s after start")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, 121))
    parser.add_argument("--dependencies", choices=("agenthub-p10-recovery", "agenthub-p10-ci"))
    args = parser.parse_args()
    if not args.base.startswith("http://127.0.0.1:"):
        parser.error("Use a disposable loopback-bound stack")
    if args.dependencies:
        assert args.base == (
            "http://127.0.0.1:18101"
            if args.dependencies == "agenthub-p10-recovery"
            else "http://127.0.0.1:18100"
        )
        dependencies(args.base, args.dependencies)
    else:
        run(args.base, args.limit)
