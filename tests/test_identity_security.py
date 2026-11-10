"""No database substitutes: offline password/schema checks and unauthenticated API behavior."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from agenthub.db.session import get_session
from agenthub.identity.schemas import CreateUserInput, LoginInput
from agenthub.identity.security import hash_password, new_token, token_digest, verify_password


def test_password_hash_and_dummy_verification() -> None:
    password = "example-test-password-123"
    first, second = hash_password(password), hash_password(password)
    assert first.startswith("$argon2id$") and first != second
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("wrong", first)
    assert not verify_password(password, None)
    assert not verify_password(password, "broken hash")


def test_token_entropy_and_digest() -> None:
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(token) == 43 and len(token_digest(token)) == 64 for token in tokens)
    assert all(token not in token_digest(token) for token in tokens)


def test_user_input_normalization_and_secret_repr() -> None:
    data = CreateUserInput.model_validate({"username": " Alice ", "password": "valid-password-123"})
    assert data.username == "alice"
    assert "valid-password-123" not in repr(data)
    assert LoginInput.model_validate({"username": "alice", "password": "short"})


@pytest.mark.parametrize(
    "updates",
    [
        {"password": "short"},
        {"password": " " * 12},
        {"password": "x" * 129},
        {"role": "owner"},
        {"username": "ab"},
        {"username": "a@b"},
        {"is_admin": True},
    ],
)
def test_user_input_rejects_invalid_data(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreateUserInput.model_validate(
            {
                "username": "alice",
                "password": "valid-password-123",
                **updates,
            }
        )


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer"])
def test_missing_authentication_never_contacts_database(
    client: TestClient, header: str | None
) -> None:
    headers = {"Authorization": header} if header else {}
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_session"


def test_auth_openapi_and_validation_do_not_leak_secrets(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/api/v1/auth/login"]["post"]
    secret = "private-password-must-not-leak"
    response = client.post("/api/v1/auth/login", json={"username": "!", "password": secret})
    assert response.status_code == 422 and secret not in response.text


def test_database_failure_is_sanitized(app: FastAPI, client: TestClient) -> None:
    def unavailable():
        raise OperationalError("private statement", {}, Exception("private-database-password"))

    app.dependency_overrides[get_session] = unavailable
    response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "x"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "private" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
