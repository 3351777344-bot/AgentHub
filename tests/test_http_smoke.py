"""Start a real HTTP server and verify failure behavior without external services."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path


def test_real_http_with_unavailable_dependencies(tmp_path: Path) -> None:
    with ExitStack() as stack:
        # Reserved, non-listening ports cannot accidentally reach a user's database/cache.
        postgres_socket = stack.enter_context(socket.socket())
        redis_socket = stack.enter_context(socket.socket())
        server_socket = stack.enter_context(socket.socket())
        for reserved in (postgres_socket, redis_socket, server_socket):
            reserved.bind(("127.0.0.1", 0))
        server_port = server_socket.getsockname()[1]
        server_socket.close()
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("AGENTHUB_")
        }
        environment.update(
            AGENTHUB_ENVIRONMENT="test",
            AGENTHUB_DATABASE_HOST="127.0.0.1",
            AGENTHUB_DATABASE_PORT=str(postgres_socket.getsockname()[1]),
            AGENTHUB_REDIS_URL=f"redis://127.0.0.1:{redis_socket.getsockname()[1]}/0",
            AGENTHUB_DEPENDENCY_TIMEOUT_SECONDS="0.1",
        )
        log = stack.enter_context((tmp_path / "server.log").open("w", encoding="utf-8"))
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agenthub.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(server_port),
                "--no-access-log",
            ],
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with opener.open(f"{base_url}/health/live", timeout=1) as response:
                        assert json.load(response) == {"status": "ok"}
                        break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(
                            "HTTP server did not start; inspect server.log"
                        ) from None
                    time.sleep(0.05)

            request = urllib.request.Request(
                f"{base_url}/health/ready", headers={"X-Request-ID": "tcp-probe"}
            )
            try:
                opener.open(request, timeout=3)
            except urllib.error.HTTPError as response:
                with response:
                    assert response.code == 503
                    assert response.headers["X-Request-ID"] == "tcp-probe"
                    error = json.load(response)["error"]
                    assert error["request_id"] == "tcp-probe"
                    assert error["details"] == {"postgresql": "error", "redis": "error"}
            else:
                raise AssertionError("Unavailable dependencies must yield HTTP 503")

            for path in ("/docs", "/openapi.json", "/health/live"):
                with opener.open(f"{base_url}{path}", timeout=3) as response:
                    assert response.status == 200
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
