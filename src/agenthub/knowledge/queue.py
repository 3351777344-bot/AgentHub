"""Redis is a bounded wake-up hint; committed PostgreSQL jobs are the queue of record."""

import logging

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from agenthub.core.config import Settings

logger = logging.getLogger("agenthub.ingestion")
QUEUE_KEY = "agenthub:ingestion:wakeup:v1"


class WakeupQueue:
    def __init__(self, settings: Settings) -> None:
        self.client: Redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=0.2,
            socket_timeout=settings.worker_poll_seconds + 1,
            retry=Retry(NoBackoff(), 0),
        )

    def notify(self) -> None:
        try:
            with self.client.pipeline(transaction=True) as pipeline:
                pipeline.lpush(QUEUE_KEY, "1")
                pipeline.ltrim(QUEUE_KEY, 0, 99)
                pipeline.execute()
        except RedisError:
            logger.warning("ingestion_wakeup_unavailable")

    def wait(self, seconds: int) -> None:
        try:
            self.client.blpop(QUEUE_KEY, timeout=seconds)
        except RedisError:
            # The caller falls back to a bounded local poll, without losing jobs.
            import time

            time.sleep(seconds)

    def close(self) -> None:
        self.client.close()
