"""Real, read-only dependency probes; no business schema initialization."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import anyio
import psycopg
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from agenthub.core.config import Settings

logger = logging.getLogger("agenthub.dependencies")
CheckStatus = Literal["ok", "error"]


class ReadinessChecker(Protocol):
    async def check(self) -> dict[str, CheckStatus]: ...


class DependencyChecks:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis: Redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.dependency_timeout_seconds,
            socket_timeout=settings.dependency_timeout_seconds,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
        )

    def _check_postgres_sync(self) -> None:
        timeout = self.settings.dependency_timeout_seconds
        # Run the synchronous driver in a worker thread for Windows event-loop compatibility.
        with psycopg.connect(
            host=self.settings.database_host,
            port=self.settings.database_port,
            dbname=self.settings.database_name,
            user=self.settings.database_user,
            password=self.settings.database_password.get_secret_value(),
            connect_timeout=max(2, math.ceil(timeout)),
            options=f"-c statement_timeout={math.ceil(timeout * 1000)}",
            autocommit=True,
        ) as connection:
            row = connection.execute(
                "SELECT 1, EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            ).fetchone()
            if row != (1, True):
                raise RuntimeError("Required vector extension is not installed")

    async def _check_postgres(self) -> None:
        await anyio.to_thread.run_sync(self._check_postgres_sync, abandon_on_cancel=True)

    async def _check_redis(self) -> None:
        if not await self.redis.ping():
            raise RuntimeError("Redis did not acknowledge PING")

    async def _run_probe(self, probe: Callable[[], Awaitable[None]]) -> CheckStatus:
        try:
            async with asyncio.timeout(self.settings.dependency_timeout_seconds):
                await probe()
        except Exception:
            # Driver exception strings commonly embed addresses and credentials.
            return "error"
        return "ok"

    async def check(self) -> dict[str, CheckStatus]:
        postgres, redis = await asyncio.gather(
            self._run_probe(self._check_postgres), self._run_probe(self._check_redis)
        )
        checks: dict[str, CheckStatus] = {"postgresql": postgres, "redis": redis}
        logger.info("dependencies_checked", extra={"checks": checks})
        return checks

    async def aclose(self) -> None:
        await self.redis.aclose()
