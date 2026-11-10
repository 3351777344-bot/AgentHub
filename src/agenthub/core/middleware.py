"""ASGI request context, including failures before response headers are sent."""

import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenthub.core.errors import error_response
from agenthub.core.logging import request_id_context

logger = logging.getLogger("agenthub.requests")
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}", flags=re.ASCII)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get("x-request-id", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                if scope.get("path", "").startswith("/api/v1/"):
                    MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            logger.error("request_failed", extra={"error_type": type(exc).__name__})
            if response_started:
                # A streaming response cannot be replaced once its headers have been sent.
                raise
            response = error_response(500, "internal_error", "Internal server error", request_id)
            await response(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "unmatched"),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
            request_id_context.reset(token)
