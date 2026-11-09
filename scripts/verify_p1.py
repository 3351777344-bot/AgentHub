"""Exercise the local AgentHub stack with real dependencies and retained volumes.

Run only against the project's local development stack. This stops dependencies
and recreates project containers, but never removes volumes or business data.
"""

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default=shutil.which("docker") or "docker")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    run_id = uuid4().hex
    output = options.output or ROOT / "data" / "acceptance" / f"p1-{run_id}.json"
    report: dict[str, object] = {"run_id": run_id, "started_at": datetime.now(UTC).isoformat()}
    events: list[dict[str, object]] = []
    report["events"] = events
    schema = f"p1_acceptance_{run_id}"
    redis_key = f"agenthub:p1-acceptance:{run_id}"
    postgres_marker_created = redis_marker_created = containers_changed = False
    stopped: set[str] = set()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def record(name: str, **evidence: object) -> None:
        events.append({"name": name, "at": datetime.now(UTC).isoformat(), **evidence})
        print(f"PASS: {name}", flush=True)

    def command(*args: str, timeout: int = 120) -> str:
        result = subprocess.run(
            [options.docker, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(f"Docker command failed: {result.stderr[-3000:]}")
        return result.stdout.strip()

    def compose(*args: str, timeout: int = 120) -> str:
        return command("compose", *args, timeout=timeout)

    def sql(query: str) -> str:
        return compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "agenthub",
            "-d",
            "agenthub",
            "-t",
            "-A",
            "-c",
            query,
        )

    def redis(*args: str) -> str:
        return compose("exec", "-T", "redis", "redis-cli", "--raw", *args)

    def container_id(service: str) -> str:
        return compose("ps", "--all", "--quiet", service)

    def probe(path: str) -> tuple[int, dict[str, Any], str]:
        request = urllib.request.Request(
            f"http://127.0.0.1:8000{path}", headers={"X-Request-ID": run_id}
        )
        try:
            response = opener.open(request, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response), response.headers.get("X-Request-ID", "")

    def wait_ready(expected_checks: dict[str, str]) -> dict[str, object]:
        expected_status = 200 if all(value == "ok" for value in expected_checks.values()) else 503
        deadline = time.monotonic() + 60
        last: object = "no response"
        while time.monotonic() < deadline:
            try:
                status, body, request_id = probe("/health/ready")
                last = {"status": status, "body": body}
                checks = (
                    body.get("checks") if status == 200 else body.get("error", {}).get("details")
                )
                if status == expected_status and checks == expected_checks:
                    assert request_id == run_id, "Response lost the request ID"
                    if status == 503:
                        assert body["error"]["request_id"] == run_id
                    return {"status": status, "checks": checks, "request_id": request_id}
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(0.5)
        raise AssertionError(f"Readiness did not reach expected state: {last}")

    def verify_markers() -> None:
        assert (
            sql(f"SELECT token, embedding <-> '[1,2,3]'::vector FROM {schema}.marker")
            == f"{run_id}|0"
        )
        assert redis("GET", redis_key) == run_id

    try:
        compose("config", "--quiet")
        baseline = wait_ready({"postgresql": "ok", "redis": "ok"})
        api_before = container_id("api")
        assert api_before, "Start the local Compose stack first"
        project = command(
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            api_before,
        )
        assert project == "agenthub", "Unexpected Compose project"
        record("baseline", **baseline)
        report["docker_version"] = json.loads(command("version", "--format", "{{json .}}"))
        report["compose_version"] = compose("version", "--short")
        report["postgresql_version"] = sql("SHOW server_version")
        report["vector_version"] = sql("SELECT extversion FROM pg_extension WHERE extname='vector'")
        report["redis_version"] = next(
            line
            for line in redis("INFO", "server").splitlines()
            if line.startswith("redis_version:")
        )
        assert compose("exec", "-T", "api", "id", "-u") != "0"
        for service in ("postgres", "redis"):
            bindings = command(
                "inspect", "--format", "{{json .HostConfig.PortBindings}}", container_id(service)
            )
            assert json.loads(bindings) in (None, {}), f"{service} unexpectedly exposes host ports"
        record("non_root_api_and_private_dependency_ports")

        tests = compose(
            "run",
            "--rm",
            "--user",
            "root",
            "--volume",
            f"{ROOT / 'tests'}:/app/tests:ro",
            "--env",
            "AGENTHUB_RUN_INTEGRATION=1",
            "api",
            "/bin/uv",
            "run",
            "--locked",
            "pytest",
            "-q",
            "-m",
            "integration",
            "tests/test_integration.py",
            timeout=600,
        )
        record("real_dependency_pytest", result=tests)

        sql(
            f"BEGIN; CREATE SCHEMA {schema}; "
            f"CREATE TABLE {schema}.marker (token text PRIMARY KEY, embedding vector(3)); "
            f"INSERT INTO {schema}.marker VALUES ('{run_id}', '[1,2,3]'); COMMIT;"
        )
        postgres_marker_created = True
        assert redis("SET", redis_key, run_id, "NX") == "OK"
        redis_marker_created = True
        verify_markers()
        record("isolated_postgresql_vector_and_redis_markers_written")

        for service, dependency in (("redis", "redis"), ("postgres", "postgresql")):
            stopped.add(service)
            compose("stop", service)
            expected = {"postgresql": "ok", "redis": "ok"}
            expected[dependency] = "error"
            failure = wait_ready(expected)
            assert probe("/health/live")[:2] == (200, {"status": "ok"})
            assert container_id("api") == api_before, "API restarted during dependency failure"
            record(f"{service}_stopped_live_200_ready_503", **failure)
            compose("start", service)
            stopped.remove(service)
            recovered = wait_ready({"postgresql": "ok", "redis": "ok"})
            verify_markers()
            assert container_id("api") == api_before
            record(f"{service}_recovered_without_api_restart", **recovered)

        logs = compose("logs", "--no-color", "api")
        password = next(
            line.partition("=")[2].strip()
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
            if line.startswith("POSTGRES_PASSWORD=")
        )
        assert password and password not in logs, "Database password exposed in API logs"
        assert run_id in logs, "Request ID missing from API logs"
        record("fault_logs_keep_request_id_without_database_password")

        ids_before = {name: container_id(name) for name in ("api", "postgres", "redis")}
        containers_changed = True
        compose("down")  # Deliberately never pass --volumes/-v.
        compose("up", "-d", "--wait", "--wait-timeout", "120", timeout=180)
        wait_ready({"postgresql": "ok", "redis": "ok"})
        ids_after = {name: container_id(name) for name in ids_before}
        assert all(ids_after[name] != ids_before[name] for name in ids_before)
        verify_markers()
        record(
            "container_recreation_preserves_postgresql_vector_and_redis_aof",
            before=ids_before,
            after=ids_after,
        )
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["error"] = str(exc)
        raise
    finally:
        cleanup_errors = []
        try:
            if containers_changed:
                compose("up", "-d", "--wait", "--wait-timeout", "120", timeout=180)
            elif stopped:
                compose("start", *sorted(stopped))
            if postgres_marker_created:
                sql(f"DROP TABLE {schema}.marker; DROP SCHEMA {schema};")
            if redis_marker_created:
                assert redis("GET", redis_key) == run_id, "Marker ownership changed"
                redis("DEL", redis_key)
            record("owned_test_markers_cleaned_and_services_restored")
        except Exception as exc:
            cleanup_errors.append(str(exc))
        report["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            report["passed"] = False
        report["finished_at"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Acceptance report: {output}", flush=True)
        if cleanup_errors:
            raise RuntimeError(f"Cleanup incomplete; inspect {output}")


if __name__ == "__main__":
    main()
