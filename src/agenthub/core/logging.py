"""Allowlisted JSON events: never serialize request bodies, URLs or exceptions."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
EVENT_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}", re.ASCII)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": (
                record.msg
                if isinstance(record.msg, str)
                and not record.args
                and EVENT_NAME.fullmatch(record.msg)
                else "unstructured_event"
            ),
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
            "agent_run_id",
            "evaluation_run_id",
            "outcome",
            "error_code",
            "published",
            "request_id",
            "embedding_ms",
            "embedding_tokens",
            "search_ms",
            "retrieved_count",
            "context_count",
            "model_ms",
        ):
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data, ensure_ascii=False)


def suppress_server_traceback(record: logging.LogRecord) -> bool:
    # RequestContextMiddleware emits a separate event before a broken stream is closed.
    if record.exc_info:
        record.msg, record.args, record.exc_info, record.exc_text = (
            "ASGI application failed",
            (),
            None,
            None,
        )
    return True


def configure_logging(level: str) -> None:
    # HTTP client logs can include upstream paths and SDK logs can include payloads.
    for name in ("mcp", "httpx", "httpx2", "httpcore", "httpcore2", "langgraph", "langsmith"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    logging.getLogger("uvicorn.error").addFilter(suppress_server_traceback)
    logger = logging.getLogger("agenthub")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
