"""Bound multipart traffic before Starlette can spool an unlimited body to disk."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenthub.core.errors import error_response


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes + 65536

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/v1/knowledge-bases/")
        ):
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length")
        if length and (not length.isdigit() or int(length) > self.max_bytes):
            response = error_response(
                413, "document_size_limit", "Upload too large", scope["state"]["request_id"]
            )
            await response(scope, receive, send)
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413)
            return message

        await self.app(scope, bounded_receive, send)
