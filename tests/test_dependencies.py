import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agenthub.core.config import Settings
from agenthub.core.dependencies import DependencyChecks
from agenthub.main import create_app


@pytest.mark.parametrize("row, expected", [((1, True), "ok"), ((1, False), "error")])
def test_postgres_probe_requires_installed_extension(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, row: tuple[int, bool], expected: str
) -> None:
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = row
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr("agenthub.core.dependencies.psycopg.connect", connect)

    async def run() -> None:
        checks = DependencyChecks(settings)
        try:
            assert await checks._run_probe(checks._check_postgres) == expected
        finally:
            await checks.aclose()

    asyncio.run(run())
    query = connection.execute.call_args.args[0]
    assert query.startswith("SELECT 1")
    assert "pg_extension" in query
    assert connect.call_args.kwargs["autocommit"] is True
    connect.return_value.__exit__.assert_called_once()


def test_probe_timeout_and_independent_results(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow() -> None:
        await asyncio.sleep(5)

    async def run() -> None:
        checks = DependencyChecks(settings)
        monkeypatch.setattr(checks, "_check_postgres", slow)
        monkeypatch.setattr(checks, "_check_redis", AsyncMock())
        try:
            async with asyncio.timeout(1):
                assert await checks.check() == {"postgresql": "error", "redis": "ok"}
        finally:
            await checks.aclose()

    asyncio.run(run())


def test_redis_failure_and_cleanup(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        checks = DependencyChecks(settings)
        ping = AsyncMock(side_effect=RuntimeError("private-password"))
        close = AsyncMock()
        monkeypatch.setattr(checks.redis, "ping", ping)
        monkeypatch.setattr(checks.redis, "aclose", close)
        assert await checks._run_probe(checks._check_redis) == "error"
        await checks.aclose()
        close.assert_awaited_once()

    asyncio.run(run())


def test_startup_checks_dependencies_and_closes_on_shutdown(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = AsyncMock(return_value={"postgresql": "error", "redis": "error"})
    close = AsyncMock()
    monkeypatch.setattr(DependencyChecks, "check", check)
    monkeypatch.setattr(DependencyChecks, "aclose", close)
    app = create_app(settings)

    async def run() -> None:
        async with app.router.lifespan_context(app):
            check.assert_awaited_once()
            assert app.state.dependencies is not None
        close.assert_awaited_once()

    asyncio.run(run())
