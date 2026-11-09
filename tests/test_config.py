from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from agenthub.core.config import Settings


def test_environment_overrides_dotenv(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENTHUB_DATABASE_PORT=5433\nPOSTGRES_PASSWORD=compose-only\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENTHUB_DATABASE_PORT", "5434")
    assert Settings(_env_file=env_file).database_port == 5434


def test_secrets_are_not_in_config_representation(settings: Settings) -> None:
    config = Settings(
        _env_file=None,
        database_password=SecretStr("private-db-password"),
        redis_url=SecretStr("redis://:private-cache-password@localhost:6379/0"),
    )
    assert "private-db-password" not in repr(config)
    assert "private-cache-password" not in repr(config)
    assert "private-db-password" not in config.model_dump_json()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"database_port": 0},
        {"database_port": 65536},
        {"dependency_timeout_seconds": 0},
        {"redis_url": "https://private-secret@example.com"},
        {"redis_url": "redis://localhost:invalid"},
        {"environment": "invalid"},
    ],
)
def test_invalid_config_fails_without_echoing_input(
    settings: Settings, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **kwargs)
    assert "input_value" not in str(error.value)
    assert "private-secret" not in str(error.value)
