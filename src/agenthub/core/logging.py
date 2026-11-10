"""Allowlisted JSON events: never serialize request bodies, URLs or exceptions."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        for name in (
            "method",
            "route",
            "status_code",
            "duration_ms",
            "error_type",
            "checks",
            "job_id",
            "error_code",
            "published",
            "request_id",
        ):
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(level: str) -> None:
    logger = logging.getLogger("agenthub")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
