"""Bound selected request bodies before HTTP parsing or disk spooling."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenthub.core.errors import error_response


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        prefixes: tuple[str, ...],
        methods: tuple[str, ...],
        code: str,
        detail: str,
    ) -> None:
        self.app, self.max_bytes = app, max_bytes
        self.prefixes, self.methods = prefixes, methods
        self.code, self.detail = code, detail

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in self.methods
            or not scope["path"].startswith(self.prefixes)
        ):
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length")
        if length and (
            len(length) > 20
            or not length.isascii()
            or not length.isdecimal()
            or int(length) > self.max_bytes
        ):
            response = error_response(413, self.code, self.detail, scope["state"]["request_id"])
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
