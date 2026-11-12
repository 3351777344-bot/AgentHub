# ruff: noqa: F811 -- reuse the disposable PostgreSQL fixtures
"""Tool contracts through FastAPI, real PostgreSQL and a real HTTPS MCP server."""

import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from test_identity_integration import (  # noqa: F401
    add_user,
    identity_client,
    identity_db,
    p2_engine,
    p2_settings,
    pytestmark,
    sign_in,
)

from agenthub.core.config import McpRemoteTarget

HOST = "mcp.p6-test.invalid"
ADD = "/api/v1/tools/add_integers/1/calls"
SERVICES = "/api/v1/mcp/services"
REGISTRATIONS = "/api/v1/tool-registrations"
AUDIT = "/api/v1/tool-calls"


def test_builtin_versions_and_private_audit(identity_client):
    client = identity_client
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    assert client.post(ADD, json={"arguments": {}}).status_code == 401
    catalog = client.get("/api/v1/tools", headers=member).json()
    assert [(item["name"], item["version"]) for item in catalog] == [
        ("add_integers", 1),
        ("knowledge_search", 1),
    ]
    response = client.post(ADD, headers=member, json={"arguments": {"left": 2, "right": 4}})
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"total": 6}
    assert (
        client.post(ADD, headers=member, json={"arguments": {"left": "2", "right": 4}}).status_code
        == 422
    )
    assert (
        client.post(ADD.replace("/1/", "/2/"), headers=member, json={"arguments": {}}).status_code
        == 404
    )
    audits = client.get(AUDIT, headers=member).json()
    assert {(row["status"], row["error_code"]) for row in audits} == {
        ("completed", None),
        ("rejected", "tool_invalid_arguments"),
        ("rejected", "tool_not_found"),
    }
    assert any(row["id"] == response.json()["call_id"] for row in audits)
    assert next(row for row in audits if row["error_code"] == "tool_not_found")["tool_name"] is None
    assert client.get(AUDIT, headers=admin).json() == []


@pytest.fixture
def remote(identity_client, tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file, marker = (tmp_path / name for name in ("cert.pem", "key.pem", "calls.txt"))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_urlsafe(24)
    target = McpRemoteTarget(url=f"https://{HOST}:{port}/mcp", ip="127.0.0.1", auth="bearer")
    client = identity_client
    client.app.state.settings = client.app.state.settings.model_copy(
        update={
            "mcp_remote_targets": {"remote": target},
            "mcp_remote_tokens": {"remote": SecretStr(token)},
            "mcp_remote_ca_file": cert_file,
        }
    )

    @contextmanager
    def running(variant="standard"):
        env = os.environ | {
            "P6_FIXTURE_VARIANT": variant,
            "P6_FIXTURE_MARKER": str(marker),
            "P6_FIXTURE_TOKEN": token,
            "P6_FIXTURE_HOST": f"{HOST}:{port}",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mcp_fixture_server:create_app",
                "--factory",
                "--app-dir",
                "tests",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--ssl-certfile",
                str(cert_file),
                "--ssl-keyfile",
                str(key_file),
                "--no-access-log",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail(f"HTTPS MCP fixture failed to start: {process.poll()}")
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

    return running, marker, token


def test_real_https_mcp_review_permissions_timeout_restart_and_drift(identity_client, remote):
    client = identity_client
    running, marker, token = remote
    admin = sign_in(client)
    add_user(client, admin)
    member = sign_in(client, "member")
    assert client.post(SERVICES, headers=member, json={"key": "remote"}).status_code == 403
    assert client.post(SERVICES, headers=admin, json={"key": "unknown"}).status_code == 404
    assert (
        client.post(
            SERVICES, headers=admin, json={"key": "remote", "url": "https://other/mcp"}
        ).status_code
        == 422
    )
    service = client.post(SERVICES, headers=admin, json={"key": "remote"})
    assert service.status_code == 201, service.text
    assert service.json() == {"key": "remote", "transport": "https", "enabled": True}
    discovery = f"{SERVICES}/remote/tools"
    endpoint = "/api/v1/tools/remote_add/1/calls"

    def invoke(left=2, *, headers=admin, path=endpoint):
        return client.post(path, headers=headers, json={"arguments": {"left": left, "right": 3}})

    with running():
        response = client.get(discovery, headers=admin)
        assert response.status_code == 200, response.text
        tools = {tool["name"]: tool for tool in response.json()}
        tool = tools["remote_add"]
        payload = {
            "name": "remote_add",
            "version": 1,
            "service_key": "remote",
            "remote_name": tool["name"],
            "definition_digest": tool["definition_digest"],
            "description": "Reviewed addition",
            "effect": "read_only",
            "timeout_seconds": 2,
        }
        mismatched = client.post(
            REGISTRATIONS, headers=admin, json=payload | {"definition_digest": "a" * 64}
        )
        assert mismatched.status_code == 409
        write = tools["remote_write"]
        mislabeled = client.post(
            REGISTRATIONS,
            headers=admin,
            json=payload
            | {"remote_name": write["name"], "definition_digest": write["definition_digest"]},
        )
        assert (
            mislabeled.status_code == 403
            and mislabeled.json()["error"]["code"] == "tool_effect_blocked"
        )
        registered = client.post(REGISTRATIONS, headers=admin, json=payload)
        assert registered.status_code == 201, registered.text
        assert registered.json()["input_schema"] == tool["input_schema"]
        assert token not in registered.text
        assert client.post(REGISTRATIONS, headers=admin, json=payload).status_code == 409
        high = client.post(
            REGISTRATIONS, headers=admin, json=payload | {"version": 2, "effect": "high"}
        )
        assert high.status_code == 201 and not high.json()["enabled"]
        assert (
            client.patch(
                f"{REGISTRATIONS}/remote_add/2", headers=admin, json={"enabled": True}
            ).status_code
            == 403
        )
        assert (
            invoke(path=endpoint.replace("/1/", "/2/")).json()["error"]["code"]
            == "tool_effect_blocked"
        )
        assert invoke(headers=member).json()["error"]["code"] == "tool_forbidden"
        assert invoke(11).json()["error"]["code"] == "tool_invalid_arguments"
        assert not marker.exists()
        result = invoke()
        assert result.status_code == 200, result.text
        assert result.json()["result"] == {"total": 5}
        failed = invoke(8)
        assert failed.status_code == 502 and "private upstream error marker" not in failed.text
        assert failed.json()["error"]["code"] == "mcp_tool_failed"
        timed_out = invoke(7)
        assert timed_out.status_code == 504, timed_out.text
        assert marker.read_text(encoding="ascii").splitlines() == ["call"] * 3

    disconnected = invoke()
    assert disconnected.status_code in (502, 504), disconnected.text
    assert client.get("/health/live").status_code == 200
    with running("redirect"):
        blocked = invoke()
        assert blocked.status_code == 502, blocked.text
        assert "redirect" not in marker.read_text(encoding="ascii")
    with running("drift"):
        drifted = invoke()
        assert drifted.status_code == 409, drifted.text
        assert drifted.json()["error"]["code"] == "mcp_definition_changed"
        assert marker.read_text(encoding="ascii").splitlines() == ["call"] * 3
    with running():
        assert invoke().json()["result"] == {"total": 5}
        off = client.patch(f"{SERVICES}/remote", headers=admin, json={"enabled": False})
        assert off.status_code == 200 and not off.json()["enabled"]
        assert invoke().json()["error"]["code"] == "mcp_service_disabled"

    for response, code in (
        (timed_out, "tool_timeout"),
        (disconnected, "mcp_unavailable" if disconnected.status_code == 502 else "tool_timeout"),
        (drifted, "mcp_definition_changed"),
    ):
        audit = client.get(
            AUDIT, headers=admin, params={"request_id": response.headers["x-request-id"]}
        )
        (row,) = audit.json()
        assert row["error_code"] == code and row["duration_ms"] >= 0
        assert token not in audit.text and "arguments" not in audit.text
