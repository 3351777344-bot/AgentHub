"""Real PostgreSQL tests. Only the dedicated, disposable P2 database may be modified."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from agenthub.core.config import Settings
from agenthub.core.dependencies import DependencyChecks
from agenthub.core.errors import AppError
from agenthub.core.logging import configure_logging
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.identity import service
from agenthub.identity.models import AuditEvent, LoginSession, User
from agenthub.identity.schemas import CreateUserInput
from agenthub.identity.security import token_digest
from agenthub.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("AGENTHUB_RUN_P2_INTEGRATION") != "1",
        reason="Set AGENTHUB_RUN_P2_INTEGRATION=1 and start compose.test.yaml",
    ),
]
PASSWORD = "P2-test-only-password-123"


@pytest.fixture(scope="module")
def p2_settings():
    return Settings(
        _env_file=None,
        environment="test",
        database_host="127.0.0.1",
        database_port=int(os.getenv("AGENTHUB_TEST_DATABASE_PORT", "55432")),
        database_name="agenthub_p2_test",
        database_user="agenthub_test",
        database_password=SecretStr(os.environ["AGENTHUB_TEST_DATABASE_PASSWORD"]),
    )


@pytest.fixture(scope="module")
def p2_engine(p2_settings):
    engine = create_database_engine(p2_settings)
    with engine.connect() as connection:
        # Explicitly reject any other database before migration/downgrade/cleanup.
        assert connection.scalar(text("select current_database()")) == "agenthub_p2_test"
    cfg = Config("alembic.ini")
    cfg.attributes["settings"] = p2_settings
    command.upgrade(cfg, "head")
    assert "users" in inspect(engine).get_table_names()
    command.downgrade(cfg, "base")
    assert "users" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # Repeated upgrade must be idempotent.
    command.check(cfg)  # Migration metadata must match the actual ORM models.
    yield engine
    engine.dispose()


@pytest.fixture
def identity_db(p2_engine):
    with p2_engine.begin() as connection:
        assert connection.scalar(text("select current_database()")) == "agenthub_p2_test"
        connection.execute(
            text(
                "TRUNCATE tool_calls, tool_registrations, mcp_services, "
                "rag_runs, document_chunks, ingestion_jobs, documents, knowledge_bases, "
                "model_calls, messages, conversations, audit_events, login_sessions, users"
            )
        )
    return create_session_factory(p2_engine)


@pytest.fixture
def identity_client(identity_db, p2_settings, monkeypatch):
    with identity_db() as db:
        admin = service.bootstrap_admin(
            db,
            CreateUserInput.model_validate(
                {
                    "username": "admin",
                    "password": PASSWORD,
                }
            ),
        )
        admin_id = str(admin.id)
    # The identity DB is real. Only the unrelated PostgreSQL/vector/Redis health
    # probe is replaced: P1 has its own separately opted-in real dependency test.
    monkeypatch.setattr(
        DependencyChecks,
        "check",
        AsyncMock(
            return_value={
                "postgresql": "ok",
                "redis": "ok",
            }
        ),
    )
    with TestClient(create_app(p2_settings)) as client:
        client.admin_id = admin_id
        yield client


def sign_in(client, username="admin", password=PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add_user(client, headers, username="member", role="member"):
    response = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_real_migration_schema(p2_engine):
    inspector = inspect(p2_engine)
    assert set(inspector.get_table_names()) == {
        "roles",
        "users",
        "login_sessions",
        "audit_events",
        "alembic_version",
        "conversations",
        "messages",
        "model_calls",
        "knowledge_bases",
        "documents",
        "ingestion_jobs",
        "document_chunks",
        "rag_runs",
        "mcp_services",
        "tool_registrations",
        "tool_calls",
    }
    assert {c["name"] for c in inspector.get_check_constraints("users")} == {"ck_users_username"}


def test_login_persistence_hashes_and_audit(identity_client, identity_db, p2_settings):
    client = identity_client
    headers = sign_in(client)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role_name"] == "admin"
    assert "hash" not in me.text and PASSWORD not in me.text
    token = headers["Authorization"].removeprefix("Bearer ")
    with identity_db() as db:
        user = db.scalar(select(User))
        stored = db.scalar(select(LoginSession))
        assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash
        assert stored.token_hash == token_digest(token) and stored.token_hash != token
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
    # A second application/pool reads the same persisted session without re-login.
    with TestClient(create_app(p2_settings)) as second:
        assert second.get("/api/v1/auth/me", headers=headers).json() == me.json()


@pytest.mark.parametrize("username,password", [("admin", "bad"), ("unknown", "bad")])
def test_bad_credentials_generic_and_audited(identity_client, identity_db, username, password):
    response = identity_client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": password,
        },
        headers={"X-Request-ID": "login-failure"},
    )
    assert response.status_code == 401 and response.json()["error"]["code"] == "invalid_credentials"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    with identity_db() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login_failed"))
        assert event.request_id == "login-failure" and event.actor_id is None
        assert db.scalar(select(func.count()).select_from(LoginSession)) == 0


@pytest.mark.parametrize("token", ["bad", "é" * 43, "x" * 44, "A" * 43])
def test_invalid_tokens(identity_client, token):
    # Non-ASCII cannot be encoded by HTTP headers; exercise the shared service directly.
    if not token.isascii():
        with pytest.raises(AppError) as exc:
            service.authenticate(None, token)
        assert exc.value.code == "invalid_session"
    else:
        assert (
            identity_client.get(
                "/api/v1/auth/me",
                headers={
                    "Authorization": f"Bearer {token}",
                },
            ).status_code
            == 401
        )


def test_expired_session(identity_client, identity_db):
    headers = sign_in(identity_client)
    with identity_db.begin() as db:
        db.execute(
            update(LoginSession).values(
                created_at=datetime.now(UTC) - timedelta(hours=2),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    assert identity_client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_logout_only_revokes_current_session(identity_client):
    first, second = sign_in(identity_client), sign_in(identity_client)
    assert identity_client.post("/api/v1/auth/logout", headers=first).status_code == 204
    assert identity_client.get("/api/v1/auth/me", headers=first).status_code == 401
    assert identity_client.post("/api/v1/auth/logout", headers=first).status_code == 401
    assert identity_client.get("/api/v1/auth/me", headers=second).status_code == 200


def test_member_permissions_and_ownership(identity_client):
    client = identity_client
    admin = sign_in(client)
    member = add_user(client, admin)
    headers = sign_in(client, "member")
    assert client.get(f"/api/v1/users/{member['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/users/{client.admin_id}", headers=headers).status_code == 403
    assert client.get(f"/api/v1/users/{uuid4()}", headers=headers).status_code == 403
    for path in ("/api/v1/users", "/api/v1/audit-events"):
        assert client.get(path, headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "username": "attacker",
                "password": PASSWORD,
                "role": "admin",
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/users/{member['id']}",
            headers=headers,
            json={
                "role": "admin",
            },
        ).status_code
        == 403
    )


def test_duplicate_username_normalization_and_pagination(identity_client):
    client = identity_client
    admin = sign_in(client)
    add_user(client, admin)
    duplicate = client.post(
        "/api/v1/users",
        headers=admin,
        json={
            "username": " MEMBER ",
            "password": PASSWORD,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "username_exists"
    assert len(client.get("/api/v1/users?limit=1&offset=1", headers=admin).json()) == 1
    assert client.get("/api/v1/users?limit=101", headers=admin).status_code == 422
    assert client.get(f"/api/v1/users/{uuid4()}", headers=admin).status_code == 404


def test_disable_reenable_and_role_change_revoke_sessions(identity_client):
    client = identity_client
    admin = sign_in(client)
    member = add_user(client, admin)
    headers = sign_in(client, "member")
    url = f"/api/v1/users/{member['id']}"
    assert client.patch(url, headers=admin, json={"is_active": False}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "member", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert client.patch(url, headers=admin, json={"is_active": True}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    fresh = sign_in(client, "member")
    assert client.patch(url, headers=admin, json={"role": "admin"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=fresh).status_code == 401
    assert client.get("/api/v1/users", headers=sign_in(client, "member")).status_code == 200


def test_admin_cannot_change_self_and_empty_patch_rejected(identity_client):
    client = identity_client
    admin = sign_in(client)
    assert (
        client.patch(
            f"/api/v1/users/{client.admin_id}",
            headers=admin,
            json={
                "is_active": False,
            },
        ).status_code
        == 409
    )
    member = add_user(client, admin)
    assert client.patch(f"/api/v1/users/{member['id']}", headers=admin, json={}).status_code == 422
    assert (
        client.patch(
            f"/api/v1/users/{member['id']}", headers=admin, json={"is_active": "false"}
        ).status_code
        == 422
    )


def test_password_change_revokes_all_sessions(identity_client):
    client = identity_client
    first, second = sign_in(client), sign_in(client)
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=first,
            json={
                "current_password": "bad",
                "new_password": "P2-new-test-password-123",
            },
        ).status_code
        == 401
    )
    assert client.get("/api/v1/auth/me", headers=second).status_code == 200
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=first,
            json={
                "current_password": PASSWORD,
                "new_password": "P2-new-test-password-123",
            },
        ).status_code
        == 204
    )
    for headers in (first, second):
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert sign_in(client, password="P2-new-test-password-123")


def test_audit_management_and_request_id(identity_client):
    client = identity_client
    headers = sign_in(client)
    response = client.post(
        "/api/v1/users",
        headers={**headers, "X-Request-ID": "create-member"},
        json={
            "username": "member",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    events = client.get("/api/v1/audit-events", headers=headers).json()
    event = next(
        event for event in events if event["action"] == "user.created" and event["actor_id"]
    )
    assert event["request_id"] == "create-member" and event["actor_id"] == client.admin_id
    assert PASSWORD not in str(events) and headers["Authorization"] not in str(events)


def test_atomic_audit_failure_rolls_back_user(identity_client, identity_db, monkeypatch):
    headers = sign_in(identity_client)

    def failed_audit(*args, **kwargs):
        raise RuntimeError("test audit failure")

    monkeypatch.setattr(service, "audit", failed_audit)
    response = identity_client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": "rollback-user",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 500
    with identity_db() as db:
        assert db.scalar(select(User).where(User.username == "rollback-user")) is None


def test_database_constraints(identity_db):
    with identity_db() as db:
        db.add(User(username="Invalid!", password_hash="irrelevant", role_name="member"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(User(username="valid-user", password_hash="irrelevant", role_name="unknown"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_two_admins_cannot_concurrently_disable_each_other(identity_client, identity_db):
    client = identity_client
    first = sign_in(client)
    second_user = add_user(client, first, username="admin2", role="admin")
    second = sign_in(client, "admin2")

    def disable(headers, user_id):
        return client.patch(
            f"/api/v1/users/{user_id}", headers=headers, json={"is_active": False}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(disable, first, second_user["id"])
        two = pool.submit(disable, second, client.admin_id)
        statuses = [one.result(), two.result()]
    assert statuses.count(200) == 1 and set(statuses) <= {200, 401, 403}
    with identity_db() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role_name == "admin",
                    User.is_active.is_(True),
                )
            )
            == 1
        )


def test_cli_initialization_and_refusal_to_overwrite(identity_db, p2_settings):
    env = {
        **os.environ,
        "AGENTHUB_DATABASE_HOST": p2_settings.database_host,
        "AGENTHUB_DATABASE_PORT": str(p2_settings.database_port),
        "AGENTHUB_DATABASE_NAME": p2_settings.database_name,
        "AGENTHUB_DATABASE_USER": p2_settings.database_user,
        "AGENTHUB_DATABASE_PASSWORD": p2_settings.database_password.get_secret_value(),
    }
    args = [
        sys.executable,
        "-m",
        "agenthub.identity.cli",
        "init-admin",
        "--username",
        "cli-admin",
        "--password-stdin",
    ]
    first = subprocess.run(
        args, input=PASSWORD + "\n", text=True, capture_output=True, env=env, timeout=15
    )
    assert first.returncode == 0 and PASSWORD not in first.stdout + first.stderr
    second = subprocess.run(
        args, input=PASSWORD + "\n", text=True, capture_output=True, env=env, timeout=15
    )
    assert second.returncode == 1 and "already initialized" in second.stderr
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_concurrent_bootstrap_initializes_only_one_admin(identity_db):
    def bootstrap(name):
        with identity_db() as db:
            try:
                service.bootstrap_admin(
                    db,
                    CreateUserInput.model_validate(
                        {
                            "username": name,
                            "password": PASSWORD,
                        }
                    ),
                )
                return "created"
            except AppError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bootstrap, ["first-admin", "other-admin"]))
    assert sorted(results) == ["already_initialized", "created"]
    with identity_db() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_authentication_logs_exclude_credentials(identity_client, p2_settings, capsys):
    # The app fixture configures its handler before capsys starts capturing.
    # Bind a fresh handler to the stream owned by this test's capture fixture.
    configure_logging("INFO")
    headers = sign_in(identity_client)
    identity_client.get("/api/v1/auth/me", headers=headers)
    output = capsys.readouterr().out
    assert '"event": "request_completed"' in output
    assert PASSWORD not in output
    assert headers["Authorization"].removeprefix("Bearer ") not in output
    assert p2_settings.database_password.get_secret_value() not in output


def test_real_tcp_session_survives_api_process_restart(identity_db, p2_settings, tmp_path):
    with identity_db() as db:
        service.bootstrap_admin(
            db,
            CreateUserInput.model_validate(
                {
                    "username": "tcp-admin",
                    "password": PASSWORD,
                }
            ),
        )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AGENTHUB_")
    }
    environment.update(
        AGENTHUB_ENVIRONMENT="test",
        AGENTHUB_DATABASE_HOST="127.0.0.1",
        AGENTHUB_DATABASE_PORT=str(p2_settings.database_port),
        AGENTHUB_DATABASE_NAME="agenthub_p2_test",
        AGENTHUB_DATABASE_USER="agenthub_test",
        AGENTHUB_DATABASE_PASSWORD=p2_settings.database_password.get_secret_value(),
        AGENTHUB_DEPENDENCY_TIMEOUT_SECONDS="0.1",
    )
    with socket.socket() as reserved_redis:
        reserved_redis.bind(("127.0.0.1", 0))
        environment["AGENTHUB_REDIS_URL"] = f"redis://127.0.0.1:{reserved_redis.getsockname()[1]}/0"

        @contextmanager
        def server():
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
            with (tmp_path / "tcp-auth.log").open("a", encoding="utf-8") as log:
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
                        str(port),
                        "--no-access-log",
                    ],
                    cwd=tmp_path,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                base = f"http://127.0.0.1:{port}"
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            with opener.open(base + "/health/live", timeout=1) as response:
                                assert response.status == 200
                            break
                        except (urllib.error.URLError, TimeoutError):
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise AssertionError(
                                    "TCP identity server failed to start"
                                ) from None
                            time.sleep(0.05)
                    yield base
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

        with server() as base:
            request = urllib.request.Request(
                base + "/api/v1/auth/login",
                data=json.dumps(
                    {
                        "username": "tcp-admin",
                        "password": PASSWORD,
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with opener.open(request, timeout=3) as response:
                token = json.load(response)["access_token"]
        with server() as base:
            request = urllib.request.Request(
                base + "/api/v1/auth/me",
                headers={
                    "Authorization": f"Bearer {token}",
                },
            )
            with opener.open(request, timeout=3) as response:
                assert json.load(response)["username"] == "tcp-admin"
        log_output = (tmp_path / "tcp-auth.log").read_text(encoding="utf-8")
        assert token not in log_output and PASSWORD not in log_output
